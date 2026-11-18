"""Trend following, breakout and mean reversion (rule sheets 1, 3, 6): BUY scenarios on hand-built bars,
neutral cases, and point in time (bar t from the full history equals the last result from bars[:t+1])."""
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_data.bars import Bar  # noqa: E402
from strategies import breakout, mean_reversion, trend  # noqa: E402
from strategies.common import finish, item  # noqa: E402

MODULES = (trend, breakout, mean_reversion)


def days(n):
    out, d = [], date(2024, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def mk(closes, vols=None, spread=0.01):
    """Bars whose open is the previous close (test data, never read by the app)."""
    ds, vols, out = days(len(closes)), vols or [1e6] * len(closes), []
    for i, (d, c) in enumerate(zip(ds, closes)):
        o = closes[i - 1] if i else c
        out.append(Bar(d, max(o, c) * (1 + spread), min(o, c) * (1 - spread), c, o, vols[i], c))
    return out


def walk(n=400, seed=2):
    rng = random.Random(seed)
    p, closes, vols = 100.0, [], []
    for _ in range(n):
        p *= 1 + rng.gauss(0.0006, 0.015)
        closes.append(p)
        vols.append(1e6 * (3 if rng.random() < 0.04 else 1) * (1 + rng.random() * 0.3))
    return mk(closes, vols, spread=0.006)


def trend_bars():
    return mk([100 + 0.4 * i + 4 * math.sin(i / 4) for i in range(320)], spread=0.004)


def breakout_bars(volume=2.5e6):
    rng = random.Random(1)
    c = [100 * (1 + 0.02 * math.sin(i / 7)) + rng.gauss(0, 1.5) for i in range(280)]
    c += [100 + rng.gauss(0, 0.2) for _ in range(20)]
    level = max(b.high for b in mk(c, spread=0.003)[-20:])
    return mk(c + [level * 1.01], [1e6] * 300 + [volume], spread=0.003)


def dip_bars():
    c = [100 * 1.002 ** i for i in range(260)]
    p = c[-1]
    return mk(c + [p * 0.96, p * 0.92, p * 0.88, p * 0.90], spread=0.005)


def test_trend_buys_a_steady_uptrend_with_rising_swings():
    out = trend.evaluate_series(trend_bars())
    last = out[-1]
    assert last["direction"] == "bullish" and last["confidence"] == 100 and last["label"] == "strong bullish"
    assert last["stopPrice"] < last["entryPrice"] < last["targetPrice"]
    assert last["targetPrice"] == pytest.approx(last["entryPrice"] + 2 * (last["entryPrice"] - last["stopPrice"]), abs=0.02)


def test_breakout_buys_a_closing_break_on_volume_after_a_squeeze():
    last = breakout.evaluate_series(breakout_bars())[-1]
    assert last["direction"] == "bullish" and last["state"] == "confirmed"
    passed = {x["key"]: x["passed"] for x in last["checklist"]}
    assert passed == {"break": True, "volume": True, "squeeze": True, "extended": True, "regime": False}  # no SPY given


def test_breakout_without_volume_confirmation_is_not_a_buy():
    last = breakout.evaluate_series(breakout_bars(volume=1.1e6))[-1]
    assert last["direction"] == "neutral" and last["state"] == "none"


def test_breakout_with_a_missing_volume_never_buys():
    bars = breakout_bars()
    bars[-5] = bars[-5]._replace(volume=None)
    last = breakout.evaluate_series(bars)[-1]
    assert last["direction"] == "neutral"
    assert next(x for x in last["checklist"] if x["key"] == "volume")["detail"] == "No volume reading"


def test_mean_reversion_buys_the_first_up_day_after_a_dip_in_an_uptrend():
    last = mean_reversion.evaluate_series(dip_bars())[-1]
    assert last["direction"] == "bullish" and last["targetPrice"] > last["entryPrice"] > last["stopPrice"]


def test_no_trade_when_the_target_is_not_above_the_entry():
    items = [item("a", "A", True, 100, "")]
    assert finish("x", "1", "X", "2026-01-02", items, ["a"], 100.0, 95.0, 99.0)["direction"] == "neutral"


@pytest.mark.parametrize("module", MODULES)
def test_too_little_history_is_neutral_not_an_error(module):
    last = module.evaluate_series(walk(100))[-1]
    assert last["direction"] == "neutral" and all(x["detail"] == "Not enough history" for x in last["checklist"])


@pytest.mark.parametrize("module", MODULES)
def test_bar_t_never_sees_later_bars(module):
    bars, spy = walk(420, 2), walk(440, 9)
    full = module.evaluate_series(bars, spy)
    for t in (290, 333, 419):
        assert full[t] == module.evaluate_series(bars[:t + 1], spy[:t + 1])[-1]


@pytest.mark.parametrize("module", MODULES)
def test_results_carry_the_contract(module):
    r = module.evaluate_series(walk(320))[-1]
    assert r["strategy"] == module.NAME and r["version"] == "1" and r["signalType"] == module.SIGNAL_TYPE
    assert {"evidence", "contradictions", "entryPrice", "stopPrice", "targetPrice", "asOf", "confidence"} <= set(r)
    assert sum(x["points"] for x in r["checklist"]) == 100
