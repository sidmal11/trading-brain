"""Approve buys in the mock account; orders, positions and close routes."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import main as api_main  # noqa: E402
from api import trading  # noqa: E402
from config import Config  # noqa: E402
from utils.settings_store import SettingsStore  # noqa: E402
from utils.storage import SqliteStore  # noqa: E402
from utils.trades_store import TradesStore  # noqa: E402

TODAY = "2026-11-17"
SETTINGS = {
    "pollIntervalMinutes": 5, "volumeThreshold": 3.0, "maxCandidatesPerRun": 5,
    "telegramAlertsEnabled": True, "markets": ["us_stocks"],
    "tradingEnabled": True, "dollarsPerTrade": 500, "maxOrdersPerDay": 3,
}


class _NoopTelegram:
    calls = []

    def update_message_decision(self, *args, **kwargs):
        _NoopTelegram.calls.append(args)


class Env:
    def __init__(self, monkeypatch, tmp_path):
        self.mp = monkeypatch
        self.price = 182.4
        self.open = 0.5
        monkeypatch.setattr(Config, "STORAGE_BACKEND", "sqlite")
        monkeypatch.setattr(Config, "SQLITE_PATH", str(tmp_path / "s.db"))
        monkeypatch.setattr(Config, "MOCK_STARTING_CASH", 10000.0)
        monkeypatch.setattr(Config, "ALLOW_UNAUTHENTICATED", True)
        monkeypatch.setattr(Config, "AUTH_MODE", "")
        monkeypatch.setattr(api_main, "_db_client", None)
        monkeypatch.setattr(api_main, "TelegramClient", _NoopTelegram)
        _NoopTelegram.calls = []
        self.trades = TradesStore(str(tmp_path / "trades.db"))
        monkeypatch.setattr(trading, "get_trades_store", lambda: self.trades)
        monkeypatch.setattr(trading, "price_of", lambda ticker: self.price)
        monkeypatch.setattr(trading, "session_fraction", lambda: self.open)
        monkeypatch.setattr(trading, "trading_date", lambda: TODAY)
        self.settings = SettingsStore(Config.SQLITE_PATH)
        self.settings.put(SETTINGS)
        self.alerts = SqliteStore(Config.SQLITE_PATH)
        self.save_alert("a1")
        self.client = TestClient(api_main.create_app())

    def save_alert(self, alert_id, ticker="AAPL", trading_date=TODAY):
        self.alerts.save_alert(alert_id, ticker, "Volume 3.4x", "Pros\n- x", trading_date=trading_date)

    def set(self, **values):
        self.settings.put({**SETTINGS, **values})

    def approve(self, alert_id="a1", **amount):
        return self.client.post(f"/api/v1/alerts/{alert_id}/review", json={"decision": "APPROVED", **amount})

    def status(self, alert_id="a1"):
        return self.alerts.get_alert(alert_id)["status"]


@pytest.fixture
def env(monkeypatch, tmp_path):
    return Env(monkeypatch, tmp_path)


SUGGESTED = 2.741228  # $500 / $182.40, rounded down to 6 decimals


def test_approve_buys_the_suggested_quantity_at_the_quote(env):
    r = env.approve()
    assert r.status_code == 200
    order = r.json()["order"]
    assert (order["quantity"], order["fillPrice"], order["status"]) == (SUGGESTED, 182.4, "FILLED")
    assert order["broker"] == "mock" and order["side"] == "BUY" and order["alertId"] == "a1"
    assert env.status() == "APPROVED"
    assert len(_NoopTelegram.calls) == 1


def test_approving_a_macd_alert_carries_its_stop_and_target_to_positions(env):
    env.alerts.save_alert("m1", "MSFT", "MACD", "x", trading_date=TODAY, strategy="macd",
                          signal_type="MACD_CROSSOVER", stop=170.0, target=200.0)
    order = env.approve("m1").json()["order"]
    assert (order["stopPrice"], order["targetPrice"]) == (170.0, 200.0)
    [position] = env.client.get("/api/v1/positions").json()["positions"]
    assert (position["stopPrice"], position["targetPrice"]) == (170.0, 200.0)


def test_second_approval_places_no_second_order(env):
    first, second = env.approve(), env.approve()
    assert first.status_code == second.status_code == 200
    assert first.json()["order"]["id"] == second.json()["order"]["id"]
    orders = env.client.get("/api/v1/orders").json()
    assert [o["status"] for o in orders] == ["FILLED"]


def test_second_alert_for_a_held_ticker_is_refused_until_close(env):
    assert env.approve().status_code == 200
    env.save_alert("a2")  # AAPL again
    r = env.approve("a2")
    assert r.status_code == 409 and r.json()["detail"] == "Already holding AAPL."
    assert env.status("a2") == "PENDING"
    mine = [o for o in env.trades.list_orders() if o["alertId"] == "a2"]
    assert [(o["status"], o["reason"]) for o in mine] == [("FAILED", "Already holding AAPL.")]
    [position] = env.client.get("/api/v1/positions").json()["positions"]
    assert position["quantity"] == SUGGESTED  # no second buy
    assert env.client.post("/api/v1/positions/AAPL/close").status_code == 200
    r = env.approve("a2")
    assert r.status_code == 200 and r.json()["order"]["status"] == "FILLED"


def test_trading_off_still_expires_an_invalid_request(env):
    env.set(tradingEnabled=False)
    env.alerts.save_alert("m1", "MSFT", "MACD", "x", trading_date=TODAY, stop=95.0, price=100.0)
    env.price = 94.0
    r = env.approve("m1")
    assert r.status_code == 409 and r.json()["detail"] == "Request expired: Price fell to the stop $95.00."
    assert env.status("m1") == "EXPIRED"


def test_repeat_approval_after_a_fill_returns_the_order_even_below_the_stop(env):
    env.alerts.save_alert("m1", "MSFT", "MACD", "x", trading_date=TODAY, stop=95.0, price=100.0)
    env.price = 100.0
    first = env.approve("m1").json()["order"]
    env.price = 94.0
    r = env.approve("m1")
    assert r.status_code == 200 and r.json()["order"]["id"] == first["id"]
    assert env.status("m1") == "APPROVED"


def test_trading_off_only_records_the_decision(env):
    env.set(tradingEnabled=False)
    r = env.approve()
    assert r.status_code == 200 and r.json()["order"] is None
    assert env.status() == "APPROVED"
    assert env.client.get("/api/v1/orders").json() == []


def _limit(env):
    env.set(maxOrdersPerDay=1)
    env.save_alert("a0", ticker="MSFT")
    assert env.approve("a0").status_code == 200


def _no_price(env):
    env.price = None


def _poor(env):
    env.mp.setattr(Config, "MOCK_STARTING_CASH", 100.0)


@pytest.mark.parametrize("arrange, reason", [
    (_limit, "Daily order limit reached (1)."),
    (_no_price, "Price unavailable."),
    (_poor, "Not enough mock cash."),
])
def test_each_limit_refuses_with_its_reason(env, arrange, reason):
    arrange(env)
    _NoopTelegram.calls = []
    r = env.approve()
    assert r.status_code == 409
    assert r.json()["detail"] == reason
    assert env.status() == "PENDING"
    mine = [o for o in env.trades.list_orders() if o["alertId"] == "a1"]
    assert [(o["status"], o["reason"]) for o in mine] == [("FAILED", reason)]
    assert _NoopTelegram.calls == []


def test_refused_then_retried_buys(env):
    env.price = None
    assert env.approve().status_code == 409
    env.price = 182.4
    r = env.approve()
    assert r.status_code == 200 and r.json()["order"]["status"] == "FILLED"
    assert [o["status"] for o in env.client.get("/api/v1/orders").json()] == ["FILLED", "FAILED"]


def test_broker_exception_marks_the_order_failed(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(trading.MockBroker, "buy", boom)
    with pytest.raises(RuntimeError):
        trading.execute_approval(env.alerts.get_alert("a1"), env.alerts)
    assert [(o["status"], o["reason"]) for o in env.trades.list_orders()] == [("FAILED", "Broker error.")]


def test_positions_and_cash_after_a_buy(env):
    env.approve()
    body = env.client.get("/api/v1/positions").json()
    assert body["broker"] == "mock" and body["startingCash"] == 10000.0
    assert body["cash"] == round(10000 - SUGGESTED * 182.4, 2)
    [position] = body["positions"]
    assert position["ticker"] == "AAPL" and position["quantity"] == SUGGESTED
    assert position["currentPrice"] == 182.4 and position["marketValue"] == round(SUGGESTED * 182.4, 2)
    assert position["unrealizedPnl"] == 0.0


def test_positions_with_quote_down_have_null_prices(env):
    env.approve()
    env.price = None
    r = env.client.get("/api/v1/positions")
    assert r.status_code == 200
    [position] = r.json()["positions"]
    for field in ("currentPrice", "marketValue", "unrealizedPnl", "unrealizedPnlPercent"):
        assert position[field] is None


def test_close_sells_the_whole_position(env):
    env.approve()
    r = env.client.post("/api/v1/positions/AAPL/close")
    assert r.status_code == 200
    order = r.json()
    assert (order["side"], order["quantity"], order["status"]) == ("SELL", SUGGESTED, "FILLED")
    assert env.client.get("/api/v1/positions").json()["positions"] == []


def test_close_without_position_is_404(env):
    assert env.client.post("/api/v1/positions/AAPL/close").status_code == 404


def test_close_after_close_is_404(env):
    env.approve()
    assert env.client.post("/api/v1/positions/AAPL/close").status_code == 200
    assert env.client.post("/api/v1/positions/AAPL/close").status_code == 404
    assert env.client.get("/api/v1/positions").json()["cash"] == 10000.0
    assert [o["side"] for o in env.trades.list_orders()] == ["SELL", "BUY"]


def test_close_with_quote_down_is_409_and_books_nothing(env):
    env.approve()
    env.price = None
    r = env.client.post("/api/v1/positions/AAPL/close")
    assert r.status_code == 409 and r.json()["detail"] == "Price unavailable."
    assert [o["side"] for o in env.trades.list_orders()] == ["BUY"]


def test_close_broker_exception_marks_the_sell_failed(env, monkeypatch):
    env.approve()

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(trading.MockBroker, "sell", boom)
    with pytest.raises(RuntimeError):
        trading.close_position("AAPL")
    sell = env.trades.list_orders()[0]
    assert (sell["side"], sell["status"], sell["reason"]) == ("SELL", "FAILED", "Broker error.")


def test_close_works_when_market_closed(env):
    env.approve()
    env.open = None
    r = env.client.post("/api/v1/positions/AAPL/close")
    assert r.status_code == 200 and r.json()["status"] == "FILLED"


def test_close_works_with_trading_off(env):
    env.approve()
    env.set(tradingEnabled=False)
    assert env.client.post("/api/v1/positions/AAPL/close").status_code == 200


def test_reject_is_unchanged(env):
    r = env.client.post("/api/v1/alerts/a1/review", json={"decision": "REJECTED"})
    assert r.status_code == 200 and r.json()["order"] is None
    assert env.status() == "REJECTED"
    assert env.trades.list_orders() == []


def test_pnl_uses_the_exact_cost_basis_not_the_rounded_average(env):
    for alert_id, price in (("b1", 33.335), ("b2", 33.335), ("b3", 33.34)):
        order, _ = env.trades.claim_buy(alert_id, "XYZ", 50, TODAY)
        env.trades.fill(order["id"], price)
    env.price = 5000.5 / 150  # unchanged: the exact average cost
    p = env.client.get("/api/v1/positions").json()["positions"][0]
    assert p["averageCost"] == 33.34
    assert p["unrealizedPnl"] == 0.0 and p["unrealizedPnlPercent"] == 0.0
    assert set(p) == {"ticker", "quantity", "averageCost", "currentPrice", "marketValue",
                      "unrealizedPnl", "unrealizedPnlPercent", "stopPrice", "targetPrice"}


@pytest.fixture
def off_sqlite(env, monkeypatch):
    monkeypatch.setattr(Config, "STORAGE_BACKEND", "dynamodb")

    def no_trades_file():
        raise AssertionError("trades file opened off SQLite")

    monkeypatch.setattr(trading, "get_trades_store", no_trades_file)
    return env


def test_orders_empty_off_sqlite(off_sqlite):
    r = off_sqlite.client.get("/api/v1/orders")
    assert r.status_code == 200 and r.json() == []


def test_positions_empty_off_sqlite(off_sqlite):
    r = off_sqlite.client.get("/api/v1/positions")
    assert r.status_code == 200
    assert r.json() == {"broker": "mock", "startingCash": 10000.0, "cash": 10000.0, "positions": []}


def test_close_is_404_off_sqlite(off_sqlite):
    assert off_sqlite.client.post("/api/v1/positions/AAPL/close").status_code == 404


# -- Amounts, any hour, validity re-check --------------------------------------

def test_dollars_buy_a_fraction_of_a_share(env):
    env.price = 153.7
    order = env.approve(dollars=10).json()["order"]
    assert (order["quantity"], order["status"]) == (0.065061, "FILLED")
    body = env.client.get("/api/v1/positions").json()
    assert body["positions"][0]["quantity"] == 0.065061
    assert body["cash"] == round(10000 - 0.065061 * 153.7, 2)


def test_shares_buy_exactly_that_many(env):
    order = env.approve(shares=0.25).json()["order"]
    assert (order["quantity"], order["status"]) == (0.25, "FILLED")


@pytest.mark.parametrize("amount", [
    {"shares": 1, "dollars": 10}, {"shares": 0}, {"dollars": -5}, {"shares": 1_000_001},
    {"dollars": 10_000_001}, {"shares": "abc"},
])
def test_bad_amounts_are_422_and_book_nothing(env, amount):
    assert env.approve(**amount).status_code == 422
    assert env.trades.list_orders() == [] and env.status() == "PENDING"


def test_non_finite_amount_is_422(env):
    r = env.client.post("/api/v1/alerts/a1/review", content='{"decision": "APPROVED", "dollars": Infinity}',
                        headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_under_one_dollar_is_refused(env):
    r = env.approve(dollars=0.5)
    assert r.status_code == 409 and r.json()["detail"] == "Minimum order is $1."
    assert env.status() == "PENDING"


def test_no_price_is_refused_before_sizing_an_amount(env):
    env.price = None
    r = env.approve(dollars=10)
    assert r.status_code == 409 and r.json()["detail"] == "Price unavailable."


def test_approval_below_the_stop_expires_the_request(env):
    env.alerts.save_alert("m1", "MSFT", "MACD", "x", trading_date=TODAY, strategy="macd",
                          signal_type="MACD_CROSSOVER", stop=95.0, target=120.0, price=100.0)
    env.price = 94.0
    r = env.approve("m1")
    assert r.status_code == 409 and r.json()["detail"] == "Request expired: Price fell to the stop $95.00."
    assert env.status("m1") == "EXPIRED"
    assert env.trades.list_orders() == []


def test_approval_after_a_run_up_expires_the_request(env):
    env.alerts.save_alert("v1", "MSFT", "Volume", "x", trading_date=TODAY, price=100.0)
    env.price = 103.0
    r = env.approve("v1")
    assert r.json()["detail"] == "Request expired: Price ran more than 3% above the alert price $100.00."
    assert env.status("v1") == "EXPIRED"


def test_buys_with_the_market_closed(env):
    env.open = None
    r = env.approve()
    assert r.status_code == 200 and r.json()["order"]["status"] == "FILLED"


def test_buys_an_alert_from_an_earlier_day(env):
    env.save_alert("a1", trading_date="2026-11-16")
    r = env.approve()
    assert r.status_code == 200 and r.json()["order"]["status"] == "FILLED"


def test_decimal_quantity_round_trips_exactly(env):
    order, _ = env.trades.claim_buy("x1", "XYZ", 0.065061, TODAY)
    assert env.trades.fill(order["id"], 153.7)["quantity"] == 0.065061
    assert env.trades.open_positions()[0]["quantity"] == 0.065061


def test_price_of_uses_the_extended_price_when_closed(monkeypatch):
    monkeypatch.setattr(trading.market_source, "quote", lambda t: {"price": 150.0, "extendedPrice": 152.5})
    monkeypatch.setattr(trading, "session_fraction", lambda: None)
    assert trading.price_of("AAPL") == 152.5
    monkeypatch.setattr(trading, "session_fraction", lambda: 0.5)
    assert trading.price_of("AAPL") == 150.0
    monkeypatch.setattr(trading, "session_fraction", lambda: None)
    monkeypatch.setattr(trading.market_source, "quote", lambda t: {"price": 150.0, "extendedPrice": None})
    assert trading.price_of("AAPL") == 150.0


def test_one_dollar_is_the_minimum_not_below_it(env):
    env.price = 153.7  # 1 / 153.7 rounds down to 0.006506 shares, $0.99996 before cent rounding
    order = env.approve(dollars=1).json()["order"]
    assert (order["quantity"], order["status"]) == (0.006506, "FILLED")


def test_dashboard_cannot_buy_a_rejected_request(env):
    env.client.post("/api/v1/alerts/a1/review", json={"decision": "REJECTED"})
    r = env.approve()
    assert r.status_code == 409 and r.json()["detail"] == "Already rejected."
    assert env.client.get("/api/v1/orders").json() == [] and env.status() == "REJECTED"


def test_dashboard_cannot_reject_a_bought_request(env):
    env.approve()
    r = env.client.post("/api/v1/alerts/a1/review", json={"decision": "REJECTED"})
    assert r.status_code == 409 and r.json()["detail"] == "Already bought. Close the position instead."
    assert env.status() == "APPROVED"
