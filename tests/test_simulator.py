"""One simulated trade, hand-calculated. Bars are (date, high, low, close, open, volume, adj_close)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtesting.simulator import CostModel, simulate  # noqa: E402
from market_data.bars import Bar  # noqa: E402

FREE = CostModel(slippage_bps=0, commission=0)


def b(i, o, h, lo, c):
    return Bar(f"2026-01-{i + 1:02d}", h, lo, c, o, 1000, c)


SIGNAL = b(0, 100, 101, 99, 100)  # bar t: close 100


def test_target_hit_exits_at_target():
    bars = [SIGNAL, b(1, 100, 104, 99, 103), b(2, 103, 111, 102, 110)]
    tr = simulate(bars, 0, stop=95, target=110, cost=FREE)
    assert (tr["entry"], tr["exit"], tr["reason"], tr["exit_date"]) == (100, 110, "target", "2026-01-03")
    assert tr["ret"] == pytest.approx(0.10)
    assert tr["bars_held"] == 2 and tr["mfe"] == pytest.approx(0.10) and tr["mae"] == pytest.approx(-0.01)  # 111 came after the exit


def test_stop_hit_exits_at_stop():
    tr = simulate([SIGNAL, b(1, 100, 101, 94, 96)], 0, stop=95, target=110, cost=FREE)
    assert (tr["exit"], tr["reason"]) == (95, "stop") and tr["ret"] == pytest.approx(-0.05)


def test_stop_first_when_one_bar_touches_both():
    tr = simulate([SIGNAL, b(1, 100, 112, 94, 105)], 0, stop=95, target=110, cost=FREE)
    assert tr["reason"] == "stop"


def test_gap_through_the_stop_fills_at_the_open():
    bars = [SIGNAL, b(1, 100, 101, 99, 100), b(2, 90, 92, 89, 91)]
    tr = simulate(bars, 0, stop=95, target=110, cost=FREE)
    assert (tr["exit"], tr["reason"]) == (90, "stop") and tr["ret"] == pytest.approx(-0.10)


def test_gap_through_the_target_fills_at_the_open():
    bars = [SIGNAL, b(1, 100, 101, 99, 100), b(2, 115, 116, 114, 115)]
    tr = simulate(bars, 0, stop=95, target=110, cost=FREE)
    assert (tr["exit"], tr["reason"]) == (115, "target")


def test_time_exit_at_the_close_of_the_last_bar():
    bars = [SIGNAL] + [b(i, 100, 101, 99, 100 + i) for i in range(1, 6)]
    tr = simulate(bars, 0, stop=95, target=200, max_hold=3, cost=FREE)
    assert (tr["exit_date"], tr["exit"], tr["reason"]) == ("2026-01-04", 103, "time")
    assert tr["mfe"] == pytest.approx(0.01) and tr["mae"] == pytest.approx(-0.01)  # the last bar was held whole


def test_entry_skipped_like_an_expired_request():
    assert simulate([SIGNAL, b(1, 94, 96, 93, 95)], 0, stop=95, target=110, cost=FREE) is None  # opens below stop
    assert simulate([SIGNAL, b(1, 104, 105, 103, 104)], 0, stop=95, target=110, cost=FREE) is None  # ran 4% > 3%
    assert simulate([SIGNAL], 0, stop=95, target=110, cost=FREE) is None  # no next bar


def test_costs_on_both_sides():
    cost = CostModel(slippage_bps=10, commission=1.0, notional=500)
    tr = simulate([SIGNAL, b(1, 100, 111, 99, 105)], 0, stop=95, target=110, cost=cost)
    assert tr["entry"] == pytest.approx(100.1) and tr["exit"] == pytest.approx(109.89)
    assert tr["ret"] == pytest.approx(109.89 / 100.1 - 1 - 2 / 500)


def test_still_open_when_history_ends():
    tr = simulate([SIGNAL, b(1, 100, 101, 99, 100)], 0, stop=95, target=110, max_hold=5, cost=FREE)
    assert tr["reason"] == "open" and tr["exit"] is None and tr["ret"] is None


def test_live_outcome_enters_at_the_given_price_and_walks_later_bars():
    bars = [SIGNAL, b(1, 90, 91, 89, 90)]  # next bar gaps below the stop
    tr = simulate(bars, 0, stop=95, target=110, cost=FREE, entry_price=100)
    assert (tr["entry"], tr["exit"], tr["reason"]) == (100, 90, "stop")
