"""Backtest runner, stored runs and live outcomes. Synthetic bars only (test data, never read by the app)."""
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtesting import runner  # noqa: E402
from backtesting.store import ResearchStore  # noqa: E402
from market_data.bar_store import BarStore  # noqa: E402
from market_data.bars import Bar  # noqa: E402
from outcomes.update import outcome_rows  # noqa: E402


def walk(n=700, seed=7):
    """A random walk with an upward drift and occasional volume spikes, on weekdays from 2023-01-02."""
    rng = random.Random(seed)
    day, price, out = date(2023, 1, 2), 100.0, []
    while len(out) < n:
        if day.weekday() < 5:
            o = price
            price *= 1 + rng.gauss(0.0008, 0.015)
            hi, lo = max(o, price) * (1 + abs(rng.gauss(0, 0.005))), min(o, price) * (1 - abs(rng.gauss(0, 0.005)))
            vol = 1_000_000 * (5 if rng.random() < 0.03 else 1) * (1 + rng.random() * 0.2)
            out.append(Bar(day.isoformat(), hi, lo, price, o, vol, price))
        day += timedelta(days=1)
    return out


@pytest.fixture(autouse=True)
def short_window(monkeypatch):
    monkeypatch.setattr(runner, "HISTORY_BARS", 250)  # MIN_BARS is 230; keeps the fixture small


def test_replay_finds_entered_trades_one_at_a_time():
    trades, skipped = runner.replay("volume", "AAA", walk())
    assert trades, "the fixture has volume spikes, so some signals must be entered"
    for a, b in zip(trades, trades[1:]):
        assert a["exit_index"] is not None and b["entry_index"] > a["exit_index"]  # never two open at once
    assert all(t["signal_date"] < t["entry_date"] for t in trades)


def test_no_future_bar_changes_an_earlier_trade():
    bars = walk()
    full, _ = runner.replay("volume", "AAA", bars)
    cut = 600
    early, _ = runner.replay("volume", "AAA", bars[:cut])
    finished = [t for t in full if t["exit_index"] is not None and t["exit_index"] < cut - 1]
    assert finished and early[:len(finished)] == finished


def test_too_little_history_gives_no_trades():
    assert runner.replay("macd", "AAA", walk(200)) == ([], 0)


def test_run_splits_in_sample_and_holdout():
    bars = walk()
    out = runner.run("volume", {"AAA": bars, "BBB": walk(seed=11)}, bars)
    ins, hold = out["windows"]["inSample"], out["windows"]["holdout"]
    assert ins["to"] < hold["from"]
    assert ins["trades"] + hold["trades"] + ins["stillOpen"] + hold["stillOpen"] == len(out["trades"])
    assert hold["benchmark"]["totalReturn"] is not None


def test_run_and_store_round_trip(tmp_path):
    path = str(tmp_path / "s.db")
    bars = walk()
    BarStore(path).upsert("AAA", bars)
    BarStore(path).upsert("SPY", bars)
    store = ResearchStore(path)
    run_id = runner.run_and_store(BarStore(path), store, "volume", ["AAA"], "2030-01-01")
    latest = store.latest_run("volume")
    assert latest["id"] == run_id and latest["strategyVersion"] == "1" and latest["backtestVersion"] == "1"
    assert set(latest["result"]) == {"inSample", "holdout"}
    assert latest["params"]["slippageBps"] == 5.0 and latest["params"]["caveats"]
    assert len(store.run_trades(run_id)) == len(runner.replay("volume", "AAA", bars)[0])
    assert store.latest_run("macd") is None


def test_live_outcome_uses_the_alert_price_and_later_bars():
    bars = [Bar("2026-11-16", 101, 99, 100, 100, 1, 100), Bar("2026-11-17", 101, 94, 96, 99, 1, 96)]
    alert = {"alertId": "a1", "ticker": "AAA", "strategy": "macd", "priceAtAlert": 100.0, "tradingDate": "2026-11-16",
             "stopPrice": 95.0, "targetPrice": 110.0}
    [row] = outcome_rows([alert], {"AAA": bars})
    assert row["reason"] == "stop" and row["exitDate"] == "2026-11-17"
    assert row["ret"] == pytest.approx(95 * 0.9995 / (100 * 1.0005) - 1)


def test_outcome_without_later_bars_stays_open_and_alerts_without_a_stop_are_skipped():
    alert = {"alertId": "a1", "ticker": "AAA", "priceAtAlert": 100.0, "tradingDate": "2026-11-17",
             "stopPrice": 95.0, "targetPrice": 110.0}
    [row] = outcome_rows([alert, {**alert, "alertId": "a2", "stopPrice": None}], {})
    assert row["alertId"] == "a1" and row["reason"] == "open" and row["ret"] is None
    assert row["strategy"] == "volume"  # alerts from before strategies were stored were volume alerts


def test_outcomes_round_trip(tmp_path):
    store = ResearchStore(str(tmp_path / "s.db"))
    row = {"alertId": "a1", "ticker": "AAA", "strategy": "macd", "signalDate": "2026-11-16", "ret": 0.1}
    store.save_outcomes([row])
    store.save_outcomes([{**row, "ret": 0.2}])
    assert [r["ret"] for r in store.outcomes("macd")] == [0.2] and store.outcomes("volume") == []



def test_a_long_trend_is_one_signal_not_one_per_day():
    from tests.test_new_strategies import trend_bars
    from strategies import trend

    bars = trend_bars()
    results = trend.evaluate_series(bars)
    streak = [t for t, r in enumerate(results) if r["direction"] == "bullish"]
    assert len(streak) > 50  # bullish for weeks...
    assert runner.new_setups(results) == [streak[0]]  # ...but one new setup


def test_series_strategies_backtest_through_the_runner():
    from tests.test_new_strategies import dip_bars

    bars = dip_bars() + walk(30, seed=4)[:0]
    trades, skipped = runner.replay("mean_reversion", "AAA", bars + bars[-1:], spy=None)
    assert skipped == 0 and all(t["bars_held"] is None or t["bars_held"] <= 10 for t in trades)
