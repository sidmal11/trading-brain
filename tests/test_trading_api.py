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

    def approve(self, alert_id="a1"):
        return self.client.post(f"/api/alerts/{alert_id}/review", json={"decision": "APPROVED"})

    def status(self, alert_id="a1"):
        return self.alerts.get_alert(alert_id)["status"]


@pytest.fixture
def env(monkeypatch, tmp_path):
    return Env(monkeypatch, tmp_path)


def test_approve_buys_whole_shares_at_the_quote(env):
    r = env.approve()
    assert r.status_code == 200
    order = r.json()["order"]
    assert (order["quantity"], order["fillPrice"], order["status"]) == (2, 182.4, "FILLED")
    assert order["broker"] == "mock" and order["side"] == "BUY" and order["alertId"] == "a1"
    assert env.status() == "APPROVED"
    assert len(_NoopTelegram.calls) == 1


def test_second_approval_places_no_second_order(env):
    first, second = env.approve(), env.approve()
    assert first.status_code == second.status_code == 200
    assert first.json()["order"]["id"] == second.json()["order"]["id"]
    orders = env.client.get("/api/orders").json()
    assert [o["status"] for o in orders] == ["FILLED"]


def test_trading_off_only_records_the_decision(env):
    env.set(tradingEnabled=False)
    r = env.approve()
    assert r.status_code == 200 and r.json()["order"] is None
    assert env.status() == "APPROVED"
    assert env.client.get("/api/orders").json() == []


def _closed(env):
    env.open = None


def _yesterday(env):
    env.save_alert("a1", trading_date="2026-11-16")


def _no_date(env):
    env.save_alert("a1", trading_date=None)


def _limit(env):
    env.set(maxOrdersPerDay=1)
    env.save_alert("a0", ticker="MSFT")
    assert env.approve("a0").status_code == 200


def _no_price(env):
    env.price = None


def _too_dear(env):
    env.price = 812.3


def _poor(env):
    env.mp.setattr(Config, "MOCK_STARTING_CASH", 100.0)


@pytest.mark.parametrize("arrange, reason", [
    (_closed, "Market is closed."),
    (_yesterday, "Alert expired: it is not from today."),
    (_no_date, "Alert expired: it is not from today."),
    (_limit, "Daily order limit reached (1)."),
    (_no_price, "Price unavailable."),
    (_too_dear, "$500 buys no whole share at $812.30."),
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
    env.open = None
    assert env.approve().status_code == 409
    env.open = 0.5
    r = env.approve()
    assert r.status_code == 200 and r.json()["order"]["status"] == "FILLED"
    assert [o["status"] for o in env.client.get("/api/orders").json()] == ["FILLED", "FAILED"]


def test_broker_exception_marks_the_order_failed(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(trading.MockBroker, "buy", boom)
    with pytest.raises(RuntimeError):
        trading.execute_approval(env.alerts.get_alert("a1"), env.alerts)
    assert [(o["status"], o["reason"]) for o in env.trades.list_orders()] == [("FAILED", "Broker error.")]


def test_positions_and_cash_after_a_buy(env):
    env.approve()
    body = env.client.get("/api/positions").json()
    assert body["broker"] == "mock" and body["startingCash"] == 10000.0
    assert body["cash"] == round(10000 - 2 * 182.4, 2)
    [position] = body["positions"]
    assert position["ticker"] == "AAPL" and position["quantity"] == 2
    assert position["currentPrice"] == 182.4 and position["marketValue"] == 364.8
    assert position["unrealizedPnl"] == 0.0


def test_positions_with_quote_down_have_null_prices(env):
    env.approve()
    env.price = None
    r = env.client.get("/api/positions")
    assert r.status_code == 200
    [position] = r.json()["positions"]
    for field in ("currentPrice", "marketValue", "unrealizedPnl", "unrealizedPnlPercent"):
        assert position[field] is None


def test_close_sells_the_whole_position(env):
    env.approve()
    r = env.client.post("/api/positions/AAPL/close")
    assert r.status_code == 200
    order = r.json()
    assert (order["side"], order["quantity"], order["status"]) == ("SELL", 2, "FILLED")
    assert env.client.get("/api/positions").json()["positions"] == []


def test_close_without_position_is_404(env):
    assert env.client.post("/api/positions/AAPL/close").status_code == 404


def test_close_when_market_closed_is_409(env):
    env.approve()
    env.open = None
    r = env.client.post("/api/positions/AAPL/close")
    assert r.status_code == 409 and r.json()["detail"] == "Market is closed."


def test_close_works_with_trading_off(env):
    env.approve()
    env.set(tradingEnabled=False)
    assert env.client.post("/api/positions/AAPL/close").status_code == 200


def test_reject_is_unchanged(env):
    r = env.client.post("/api/alerts/a1/review", json={"decision": "REJECTED"})
    assert r.status_code == 200 and r.json()["order"] is None
    assert env.status() == "REJECTED"
    assert env.trades.list_orders() == []
