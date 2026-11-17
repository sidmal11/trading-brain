import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategies.indicators import checklist, ema, macd, macd_buy, sma  # noqa: E402

KEYS = ["uptrend", "macd", "volume", "price_action", "reward_risk"]
POINTS = {"uptrend": 25, "macd": 25, "volume": 20, "price_action": 15, "reward_risk": 15}


def day(i):
    return (date(2025, 1, 1) + timedelta(days=i)).isoformat()


def bars_from(closes, highs=None, lows=None):
    highs = highs or [c + 1 for c in closes]
    lows = lows or [c - 1 for c in closes]
    return [(day(i), highs[i], lows[i], closes[i]) for i in range(len(closes))]


def item(result, key):
    return next(x for x in result["checklist"] if x["key"] == key)


def run(bars, entry=None, volume_ok=False, volume_detail="Volume 1.0x vs 3.0x"):
    return checklist(bars, entry, volume_ok, volume_detail)


def rising(n=260):
    return [100.0 + i for i in range(n)]


# ---------- indicators ----------

def test_sma_and_ema_match_hand_values():
    assert sma([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]
    # alpha = 2 / (3 + 1) = 0.5, seeded with the first value
    assert ema([1, 2, 3, 4, 5], 3) == pytest.approx([1, 1.5, 2.25, 3.125, 4.0625])


def test_macd_matches_pandas_reference():
    closes = [100 + 10 * ((i * 7) % 13) / 13 + i * 0.3 for i in range(120)]
    s = pd.Series(closes, dtype=float)
    ref_macd = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    ref_sig = ref_macd.ewm(span=9, adjust=False).mean()
    m, sig, hist = macd(closes)
    assert m == pytest.approx(ref_macd.tolist())
    assert sig == pytest.approx(ref_sig.tolist())
    assert hist == pytest.approx((ref_macd - ref_sig).tolist())


# ---------- uptrend ----------

def test_uptrend_passes_on_rising_series_and_fails_when_choppy():
    up = item(run(bars_from(rising())), "uptrend")
    assert up["passed"] and up["points"] == 25
    assert "0 crosses in 30 days" in up["detail"]

    choppy = rising()
    n = len(choppy)
    for i in (n - 25, n - 15):  # one-bar dips: crosses at n-25, n-24, n-15, n-14
        choppy[i] = 1.0
    up = item(run(bars_from(choppy)), "uptrend")
    assert not up["passed"]
    assert "4 crosses in 30 days" in up["detail"]


def _cross_series(first_dip):
    # Dip bar at first_dip gives crosses at first_dip and first_dip + 1; a dip at
    # n-15 adds 2 more. Window = crosses landing on the last 30 bars (n-30..n-1).
    closes = rising()
    n = len(closes)
    closes[n + first_dip] = 1.0
    closes[n - 15] = 1.0
    return bars_from(closes)


def test_uptrend_cross_window_is_exactly_thirty_bars():
    # crosses at n-31 (out), n-30 (in), n-15, n-14 -> 3 in window -> fails
    up = item(run(_cross_series(-31)), "uptrend")
    assert not up["passed"] and "3 crosses in 30 days" in up["detail"]
    # crosses at n-32, n-31 (both out), n-15, n-14 -> 2 in window -> passes
    up = item(run(_cross_series(-32)), "uptrend")
    assert up["passed"] and "2 crosses in 30 days" in up["detail"]


def test_uptrend_compares_sma_exactly_twenty_bars_earlier():
    # A +5000 close lifts SMA200 by 25 for the 200 bars that include it; the
    # linear rise only adds 20 over 20 bars.
    n = 260
    closes = rising(n)
    closes[n - 220] += 5000  # inside SMA[n-21], outside SMA[n-20]
    assert not item(run(bars_from(closes)), "uptrend")["passed"]

    closes = rising(n)
    closes[n - 221] += 5000  # inside SMA[n-22], outside SMA[n-21]
    assert item(run(bars_from(closes)), "uptrend")["passed"]


# ---------- MACD ----------

def _macd_series():
    # accelerating decline, then accelerating rise: one clean MACD cross up
    closes = [300 - 0.002 * i * i for i in range(250)]
    base = closes[-1]
    closes += [base + 0.05 * (j + 1) ** 2 for j in range(50)]
    s = pd.Series(closes)
    m = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    sig = m.ewm(span=9, adjust=False).mean()
    h = (m - sig).tolist()
    crosses = [i for i in range(1, len(closes)) if m[i - 1] <= sig[i - 1] and m[i] > sig[i]]
    late = [i for i in crosses if i >= 200]
    assert len(late) == 1
    c = late[0]
    assert 0 < h[c] < h[c + 1] < h[c + 2] < h[c + 3]  # histogram rising either way
    return closes, c


def test_macd_cross_window_is_exactly_three_bars():
    closes, c = _macd_series()
    # cross on the oldest of the last 3 bars -> passes
    res = item(run(bars_from(closes[: c + 3])), "macd")
    assert res["passed"] and res["points"] == 25
    assert "2 days ago" in res["detail"]
    # cross 4 bars back -> fails
    res = item(run(bars_from(closes[: c + 4])), "macd")
    assert not res["passed"]
    # cross on the last bar itself -> passes
    assert item(run(bars_from(closes[: c + 1])), "macd")["passed"]


def test_macd_needs_rising_positive_histogram():
    closes, c = _macd_series()
    closes = closes[: c + 3]
    closes[-1] = closes[-2] - 5  # sharp drop: histogram falls on the last bar
    assert not item(run(bars_from(closes)), "macd")["passed"]


# ---------- price action ----------

def test_breakout_within_3_percent_passes_and_beyond_fails():
    # rising series: lows stay above EMA20 so the pullback branch cannot pass
    closes = rising()
    prior_high = closes[-2] + 1
    closes[-1] = prior_high * 1.02
    pa = item(run(bars_from(closes)), "price_action")
    assert pa["passed"] and pa["points"] == 15

    closes[-1] = prior_high * 1.03  # exactly 3% passes
    assert item(run(bars_from(closes)), "price_action")["passed"]

    for over in (1.031, 1.04):
        closes[-1] = prior_high * over
        assert not item(run(bars_from(closes)), "price_action")["passed"]

    closes[-1] = prior_high  # equal, not above
    assert not item(run(bars_from(closes)), "price_action")["passed"]


def test_breakout_high_window_is_exactly_twenty_bars_before_last():
    closes = rising()
    n = len(closes)
    closes[-1] = (closes[-2] + 1) * 1.02
    highs = [c + 1 for c in closes]
    highs[n - 21] = 10_000  # oldest of the 20 bars before the last -> blocks breakout
    assert not item(run(bars_from(closes, highs=highs)), "price_action")["passed"]

    highs = [c + 1 for c in closes]
    highs[n - 22] = 10_000  # 21 bars before the last -> ignored
    assert item(run(bars_from(closes, highs=highs)), "price_action")["passed"]


def test_pullback_to_ema20_then_higher_close_passes():
    closes = rising()
    n = len(closes)

    def with_low_at(idx):
        lows = [c - 1 for c in closes]
        lows[idx] = 0.0  # far below EMA20
        return run(bars_from(closes, lows=lows))

    assert item(with_low_at(n - 6), "price_action")["passed"]  # oldest of 5 before last
    assert item(with_low_at(n - 2), "price_action")["passed"]
    assert not item(with_low_at(n - 7), "price_action")["passed"]  # 6 before last
    assert not item(with_low_at(n - 1), "price_action")["passed"]  # last bar itself

    # pullback bar closed above the last close -> fails
    closes2 = list(closes)
    closes2[n - 3] = closes2[-1] + 50
    lows = [c - 1 for c in closes2]
    lows[n - 3] = 0.0
    assert not item(run(bars_from(closes2, lows=lows)), "price_action")["passed"]


# ---------- reward / risk ----------

def _flat_with_lows(low_by_offset):
    n = 240
    closes = [100.0] * n
    lows = [99.5] * n
    for off, v in low_by_offset.items():
        lows[n + off] = v
    return bars_from(closes, highs=[100.5] * n, lows=lows)


def test_stop_target_and_8_percent_rule():
    res = run(_flat_with_lows({-1: 95.0}), entry=100)
    rr = item(res, "reward_risk")
    assert rr["passed"] and rr["points"] == 15
    assert res["entryPrice"] == 100.0
    assert res["stopPrice"] == 95.0
    assert res["targetPrice"] == 107.5

    res = run(_flat_with_lows({-1: 91.0}), entry=100)
    assert not item(res, "reward_risk")["passed"]
    assert res["stopPrice"] == 91.0
    assert res["targetPrice"] == 113.5

    res = run(_flat_with_lows({-1: 92.0}), entry=100)  # exactly 8% passes
    assert item(res, "reward_risk")["passed"]

    res = run(_flat_with_lows({}), entry=99.0)  # lowest low 99.5 >= entry
    assert not item(res, "reward_risk")["passed"]
    assert res["stopPrice"] is None and res["targetPrice"] is None


def test_stop_window_is_exactly_last_ten_bars():
    res = run(_flat_with_lows({-10: 91.0, -5: 95.0}), entry=100)
    assert res["stopPrice"] == 91.0  # oldest of the last 10 counts
    res = run(_flat_with_lows({-11: 91.0, -5: 95.0}), entry=100)
    assert res["stopPrice"] == 95.0  # 11 bars back is ignored


def test_entry_defaults_to_last_close_and_money_is_rounded():
    n = 240
    closes = [100.0] * (n - 1) + [100.123]
    lows = [99.5] * (n - 1) + [96.456]
    res = run(bars_from(closes, highs=[101] * n, lows=lows))
    assert res["entryPrice"] == 100.12
    assert res["stopPrice"] == round(96.456, 2)
    assert res["targetPrice"] == round(100.123 + 1.5 * (100.123 - 96.456), 2)


# ---------- totals, short history, BUY rule ----------

def test_confidence_is_sum_of_passed_points():
    bars = bars_from(rising())
    for vol in (True, False):
        res = run(bars, volume_ok=vol, volume_detail="Volume 3.5x vs 3.0x")
        assert [x["key"] for x in res["checklist"]] == KEYS
        assert all(x["points"] == POINTS[x["key"]] for x in res["checklist"])
        assert res["confidence"] == sum(x["points"] for x in res["checklist"] if x["passed"])
        v = item(res, "volume")
        assert v["passed"] is vol and v["detail"] == "Volume 3.5x vs 3.0x"
    with_vol = run(bars, volume_ok=True)["confidence"]
    without = run(bars, volume_ok=False)["confidence"]
    assert with_vol - without == 20


def test_labels_and_no_em_dash():
    res = run(bars_from(rising()))
    assert [x["label"] for x in res["checklist"]] == [
        "Uptrend: above a rising 200-day average, not choppy",
        "MACD crossed up in the last 3 days",
        "Unusual volume",
        "Breakout or pullback",
        "Stop within 8%, target 1.5x the risk",
    ]
    assert all("—" not in x["detail"] and "—" not in x["label"] for x in res["checklist"])


def test_short_history_fails_everything():
    for n in (229, 0):
        res = run(bars_from(rising(n)), volume_ok=True)
        assert [x["key"] for x in res["checklist"]] == KEYS
        assert all(not x["passed"] and x["detail"] == "Not enough history" for x in res["checklist"])
        assert res["confidence"] == 0
        assert res["stopPrice"] is None and res["targetPrice"] is None
        assert not macd_buy(res)
    res = run(bars_from(rising(230)))
    assert all(x["detail"] != "Not enough history" for x in res["checklist"])


def _result(passed):
    return {"checklist": [{"key": k, "passed": k in passed} for k in KEYS]}


def test_macd_buy_needs_all_but_volume():
    need = {"uptrend", "macd", "price_action", "reward_risk"}
    assert macd_buy(_result(need))
    assert macd_buy(_result(need | {"volume"}))
    for k in need:
        assert not macd_buy(_result((need - {k}) | {"volume"}))
