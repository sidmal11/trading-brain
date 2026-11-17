import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from utils.trades_store import TradesStore

DAY = "2026-11-17"


@pytest.fixture
def store(tmp_path):
    return TradesStore(str(tmp_path / "trades.db"))


def _filled_buy(store, alert_id, qty, price, day=DAY, **kw):
    order, _ = store.claim_buy(alert_id, "AAPL", qty, day, **kw)
    return store.fill(order["id"], price)


def _filled_sell(store, qty, price, day=DAY):
    return store.fill(store.claim_sell("AAPL", qty, day)["id"], price)


def test_claim_buy_twice_returns_the_same_order(store):
    first, created1 = store.claim_buy("a1", "AAPL", 2, DAY, strategy="volume")
    second, created2 = store.claim_buy("a1", "AAPL", 2, DAY, strategy="volume")
    assert created1 is True and created2 is False
    assert second["id"] == first["id"]
    assert len(store.list_orders()) == 1
    assert first == {
        "id": first["id"], "alertId": "a1", "ticker": "AAPL", "side": "BUY", "quantity": 2,
        "status": "PENDING", "fillPrice": None, "reason": None, "broker": "mock", "strategy": "volume",
        "stopPrice": None, "targetPrice": None, "tradingDate": DAY, "createdAt": first["createdAt"],
    }


def test_failed_buy_does_not_block_a_retry(store):
    first, _ = store.claim_buy("a1", "AAPL", 2, DAY)
    failed = store.fail(first["id"], "Market is closed.")
    assert failed["status"] == "FAILED" and failed["reason"] == "Market is closed."
    second, created = store.claim_buy("a1", "AAPL", 2, DAY)
    assert created is True
    assert second["id"] != first["id"]


def test_position_and_cash_after_buy_buy_close(store):
    _filled_buy(store, "a1", 2, 100.0)
    _filled_buy(store, "a2", 2, 110.0, stop_price=99.0, target_price=130.0)
    assert store.open_positions() == [
        {"ticker": "AAPL", "quantity": 4, "averageCost": 105.0, "stopPrice": 99.0, "targetPrice": 130.0}
    ]
    assert store.cash(10000) == 9580.0
    sell = _filled_sell(store, 4, 120.0)
    assert sell["side"] == "SELL" and sell["alertId"] is None
    assert store.open_positions() == []
    assert store.cash(10000) == 10060.0


def test_rebuy_after_close_starts_a_fresh_average(store):
    _filled_buy(store, "a1", 2, 100.0)
    _filled_buy(store, "a2", 2, 110.0)
    _filled_sell(store, 4, 120.0)
    _filled_buy(store, "a3", 1, 130.0)
    [pos] = store.open_positions()
    assert pos["quantity"] == 1 and pos["averageCost"] == 130.0


def test_pending_and_failed_orders_do_not_count(store):
    store.claim_buy("a1", "AAPL", 2, DAY)
    failed, _ = store.claim_buy("a2", "AAPL", 2, DAY)
    store.fail(failed["id"], "Price unavailable.")
    assert store.open_positions() == []
    assert store.cash(10000) == 10000.0


def test_filled_buys_on_counts_only_filled_buys_that_day(store):
    _filled_buy(store, "a1", 1, 100.0)
    _filled_buy(store, "a2", 1, 100.0, day="2026-11-16")
    store.claim_buy("a3", "AAPL", 1, DAY)
    failed, _ = store.claim_buy("a4", "AAPL", 1, DAY)
    store.fail(failed["id"], "Broker error.")
    _filled_sell(store, 1, 100.0)
    assert store.filled_buys_on(DAY) == 1


def test_orders_survive_a_new_store_object(tmp_path):
    path = str(tmp_path / "trades.db")
    order, _ = TradesStore(path).claim_buy("a1", "AAPL", 2, DAY)
    again = TradesStore(path)
    assert [o["id"] for o in again.list_orders()] == [order["id"]]
    assert again.claim_buy("a1", "AAPL", 2, DAY) == (order, False)


def test_list_orders_is_newest_first(store):
    a, _ = store.claim_buy("a1", "AAPL", 1, DAY)
    b = store.claim_sell("AAPL", 1, DAY)
    assert [o["id"] for o in store.list_orders()] == [b["id"], a["id"]]
