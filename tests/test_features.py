"""Features: price action rules on hand-built bars, relative strength, regime, determinism, no future data."""
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from features.build import FEATURE_VERSION, FeatureStore, build_features  # noqa: E402
from features.price_action import candlesticks, price_action, structure, swings  # noqa: E402
from features.regime import regime  # noqa: E402
from features.relative_strength import relative_returns  # noqa: E402
from market_data.bars import Bar  # noqa: E402


def days(n, start=date(2024, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def bars_from(closes, spread=1.0, opens=None):
    ds = days(len(closes))
    opens = opens or closes
    return [Bar(d, max(o, c) + spread, min(o, c) - spread, c, o, 1000.0, c) for d, o, c in zip(ds, opens, closes)]


def walk(n=500, seed=5, drift=0.0005):
    rng = random.Random(seed)
    price, out = 100.0, []
    for d in days(n):
        o = price
        price *= 1 + rng.gauss(drift, 0.012)
        out.append(Bar(d, max(o, price) * 1.004, min(o, price) * 0.996, price, o, 1e6 * (1 + rng.random()), price))
    return out


# ── price action ──────────────────────────────────────────────────────────────

def test_swings_need_two_bars_after_them():
    #        0   1   2   3   4   5   6
    c = [10, 11, 15, 12, 11, 13, 16]
    highs, lows = swings(bars_from(c, spread=0))
    assert highs == [(2, 15)]  # 6 is the newest bar: not confirmed
    assert lows == [(4, 11)]


def test_swings_found_early_never_change_with_later_bars():
    b = walk(300)
    full_h, full_l = swings(b)
    for t in (100, 180, 250):
        h, lo = swings(b[:t + 1])
        assert h == [x for x in full_h if x[0] <= t - 2] and lo == [x for x in full_l if x[0] <= t - 2]


def test_structure_labels():
    assert structure([(1, 10), (5, 12)], [(3, 8), (7, 9)]) == "up"
    assert structure([(1, 12), (5, 10)], [(3, 9), (7, 8)]) == "down"
    assert structure([(1, 10), (5, 12)], [(3, 9), (7, 8)]) == "mixed"
    assert structure([(1, 10)], [(3, 9), (7, 8)]) is None


def candle(o, h, lo, c, d="2026-01-02"):
    return Bar(d, h, lo, c, o, 1000.0, c)


def test_single_bar_candles():
    assert "doji" in candlesticks([candle(10, 11, 9, 10.05)])
    assert "hammer" in candlesticks([candle(10, 10.6, 8, 10.5)])  # body 0.5, lower shadow 2, upper 0.1
    assert "shooting_star" in candlesticks([candle(10.5, 13, 9.9, 10)])


def test_two_and_three_bar_candles():
    assert "bullish_engulfing" in candlesticks([candle(11, 11.2, 9.8, 10), candle(9.9, 11.6, 9.8, 11.5)])
    assert "bearish_engulfing" in candlesticks([candle(10, 11.2, 9.8, 11), candle(11.1, 11.2, 9.4, 9.5)])
    assert "inside_bar" in candlesticks([candle(10, 12, 8, 11), candle(10.5, 11, 9, 10.8)])
    assert "outside_bar" in candlesticks([candle(10, 11, 9, 10.5), candle(10.4, 12, 8, 11)])
    star = [candle(12, 12.1, 9.9, 10), candle(9.8, 10, 9.5, 9.7), candle(9.8, 11.6, 9.7, 11.5)]
    assert "morning_star" in candlesticks(star)


def test_breakout_gap_and_levels():
    c = [10.0] * 25 + [10.5, 12.0]
    pa = price_action(bars_from(c, spread=0.2, opens=c[:-1] + [11.0]))
    assert pa["breakout"] == "up" and pa["gapPct"] == pytest.approx((11.0 / 10.5 - 1) * 100)
    b = walk(400)
    pa = price_action(b)
    close = b[-1].close
    if pa["nearestSupport"]:
        assert pa["nearestSupport"]["price"] < close and pa["nearestSupport"]["reactions"] >= 1
    if pa["nearestResistance"]:
        assert pa["nearestResistance"]["price"] > close


# ── relative strength and regime ──────────────────────────────────────────────

def test_relative_returns_hand_case_and_future_ignored():
    t = bars_from([100, 110])            # +10%
    s = bars_from([200, 210, 999])       # +5%, then a later bar that must be ignored
    out = relative_returns(t, s, periods=(1,))
    assert out[1] == pytest.approx(5.0)
    assert relative_returns(t, [], periods=(1,)) == {1: None}
    assert relative_returns(bars_from([100]), s, periods=(1,)) == {1: None}


def test_regime_bull_on_a_steady_rise_and_future_ignored():
    rise = bars_from([100 * 1.002 ** i for i in range(300)], spread=0.3)
    r = regime(rise)
    assert r["trend"] == "bull" and r["character"] in ("trending", "mixed", "mean_reverting")
    later = [Bar(f"2030-01-{i + 1:02d}", 60, 40, 50, 55, 1.0, 50) for i in range(30)]  # a crash after the day
    assert regime(rise + later, as_of=rise[-1].date) == r
    assert regime(rise, as_of=rise[250].date)["asOf"] == rise[250].date


def test_regime_bear_on_a_steady_fall():
    fall = bars_from([300 * 0.998 ** i for i in range(300)], spread=0.3)
    assert regime(fall)["trend"] == "bear"


# ── the feature dict ──────────────────────────────────────────────────────────

def test_features_are_deterministic_and_versioned():
    b, spy = walk(500, 5), walk(500, 9)
    one, two = build_features(b, spy, spy, "XLK"), build_features(b, spy, spy, "XLK")
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)
    assert one["version"] == FEATURE_VERSION and one["asOf"] == b[-1].date
    assert 0.9 <= one["coverage"] <= 1.0


def test_later_benchmark_bars_change_nothing():
    b, spy = walk(500, 5), walk(520, 9)
    assert build_features(b[:400], spy) == build_features(b[:400], spy[:400])


def test_short_history_lowers_coverage_without_inventing():
    f = build_features(walk(60))
    assert f["trend"]["priceVsSma200Pct"] is None and f["coverage"] < 0.8


def test_missing_volume_leaves_volume_features_empty():
    b = walk(300)
    b[-1] = b[-1]._replace(volume=None)
    f = build_features(b)
    assert f["volume"] == {"relVolume20": None, "obvTrend20": None, "mfi14": None, "cmf20": None}


def test_store_keeps_the_latest_per_version(tmp_path):
    store = FeatureStore(str(tmp_path / "s.db"))
    b = walk(300)
    store.save("AAA", build_features(b[:290]))
    store.save("AAA", build_features(b))
    assert store.latest("AAA")["asOf"] == b[-1].date and store.latest("BBB") is None
