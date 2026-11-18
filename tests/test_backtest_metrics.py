"""Backtest metrics on hand-built trades."""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtesting.metrics import buy_and_hold, equity_stats, trade_stats  # noqa: E402
from market_data.bars import Bar  # noqa: E402

DATES = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
CLOSES = [100, 105, 110, 99, 99]
A = [Bar(d, c + 1, c - 1, c, c, 1000, c) for d, c in zip(DATES, CLOSES)]


def trade(entry_index, entry, exit_index, exit_, ticker="A"):
    return {"ticker": ticker, "entry_index": entry_index, "entry": entry, "exit_index": exit_index, "exit": exit_,
            "ret": exit_ / entry - 1, "bars_held": exit_index - entry_index + 1}


WIN = trade(1, 100.0, 2, 110.0)   # +10%
LOSS = trade(3, 100.0, 4, 95.0)   # -5% (entry at 100 on a 99 close day)


def test_trade_stats():
    s = trade_stats([WIN, LOSS, {**LOSS, "ret": None}])  # an open trade is left out
    assert s["trades"] == 2 and s["winRate"] == 0.5
    assert s["avgWin"] == pytest.approx(0.10) and s["avgLoss"] == pytest.approx(-0.05)
    assert s["expectancy"] == pytest.approx(0.025) and s["profitFactor"] == pytest.approx(2.0)
    assert s["avgBarsHeld"] == 2


def test_no_trades_gives_nulls_not_zeros():
    s = trade_stats([])
    assert s["trades"] == 0 and s["winRate"] is None and s["profitFactor"] is None


def test_equity_marks_each_held_day():
    s = equity_stats([WIN], {"A": A}, DATES, weight=0.5)
    # day 1: 105/100, day 2: 110/105, each at half weight; other days flat
    expected = (1 + 0.5 * 0.05) * (1 + 0.5 * (110 / 105 - 1)) - 1
    assert s["totalReturn"] == pytest.approx(expected)
    assert s["exposure"] == pytest.approx(2 / 5) and s["maxDrawdown"] == 0


def test_drawdown_after_a_loss():
    s = equity_stats([WIN, LOSS], {"A": A}, DATES, weight=1.0)
    # LOSS: day 3 99/100, day 4 95/99
    peak = 1.05 * (110 / 105)
    trough = peak * 0.99 * (95 / 99)
    assert s["maxDrawdown"] == pytest.approx(trough / peak - 1)


def test_buy_and_hold_uses_adjusted_close():
    s = buy_and_hold(A, DATES)
    assert s["totalReturn"] == pytest.approx(99 / 100 - 1)
    assert s["sharpe"] is not None and math.isfinite(s["sharpe"])


def test_empty_window_is_null():
    assert equity_stats([], {"A": A}, [], weight=0.05)["totalReturn"] is None
