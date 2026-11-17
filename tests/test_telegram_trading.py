"""Telegram Approve tap buys in the mock account, like the dashboard."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import main as api_main  # noqa: E402
from config import Config  # noqa: E402
from tests.test_trading_api import Env  # noqa: E402
from utils import telegram_client as tc  # noqa: E402

ADMIN = "42"
HEADERS = {"X-Telegram-Bot-Api-Secret-Token": "s3cret"}


class Http:
    def __init__(self):
        self.calls = []

    def __call__(self, url, json=None, timeout=None):
        self.calls.append((url.rsplit("/", 1)[-1], json or {}))

        class R:
            status_code = 200

            def json(self):
                return {"ok": True, "result": {"message_id": 1}}

        return R()

    def texts(self):
        return [p["text"] for m, p in self.calls if m == "sendMessage"]

    def methods(self):
        return [m for m, _ in self.calls]


@pytest.fixture
def tg(monkeypatch, tmp_path):
    env = Env(monkeypatch, tmp_path)
    http = Http()
    monkeypatch.setattr(api_main, "TelegramClient", tc.TelegramClient)
    monkeypatch.setattr(tc.requests, "post", http)
    monkeypatch.setattr(Config, "TELEGRAM_BOT_TOKEN", "1:fake")
    monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", ADMIN)
    monkeypatch.setattr(Config, "ADMIN_CHAT_IDS", "")
    monkeypatch.setattr(Config, "TELEGRAM_WEBHOOK_SECRET", "s3cret")
    env.http = http

    def tap(decision="approve", alert_id="a1", ticker="AAPL"):
        return env.client.post("/api/telegram/webhook", headers=HEADERS, json={"callback_query": {
            "id": "cb", "data": f"{decision}:{alert_id}:{ticker}", "from": {"id": int(ADMIN)},
            "message": {"message_id": 7, "chat": {"id": int(ADMIN)}},
        }})

    env.tap = tap
    return env


def _answers(http):
    return [p["text"] for m, p in http.calls if m == "answerCallbackQuery"]


def test_tap_approve_buys_and_replies(tg):
    assert tg.tap().status_code == 200
    assert "Bought 2 AAPL at $182.40 (MOCK)." in tg.http.texts()
    assert _answers(tg.http) == ["Bought 2 AAPL (MOCK)"]
    assert "editMessageReplyMarkup" in tg.http.methods()
    orders = tg.client.get("/api/orders").json()
    assert [o["status"] for o in orders] == ["FILLED"]
    assert tg.status() == "APPROVED"


def test_tap_then_dashboard_approve_places_one_order(tg):
    tg.tap()
    r = tg.approve()
    orders = tg.client.get("/api/orders").json()
    assert r.status_code == 200 and len(orders) == 1
    assert r.json()["order"]["id"] == orders[0]["id"] and orders[0]["status"] == "FILLED"


def test_refused_tap_replies_with_reason_and_keeps_buttons(tg):
    tg.open = None
    assert tg.tap().status_code == 200
    assert tg.http.texts() == ["AAPL not bought: Market is closed."]
    assert _answers(tg.http) == ["Market is closed."]
    assert "editMessageReplyMarkup" not in tg.http.methods()
    assert tg.status() == "PENDING"


def test_tap_with_trading_off_is_unchanged(tg):
    tg.set(tradingEnabled=False)
    assert tg.tap().status_code == 200
    assert tg.client.get("/api/orders").json() == []
    assert not any("Bought" in t for t in tg.http.texts() + _answers(tg.http))
    assert _answers(tg.http) == ["AAPL: Approved"]
    assert tg.status() == "APPROVED"


def test_tap_reject_is_unchanged(tg):
    tg.tap("reject")
    assert tg.status() == "REJECTED"
    assert tg.client.get("/api/orders").json() == []
    assert _answers(tg.http) == ["AAPL: Rejected"]


def test_tap_while_order_in_flight_does_not_claim_a_buy(tg):
    tg.trades.claim_buy("a1", "AAPL", 2, "2026-11-17")  # another request holds a PENDING order
    tg.tap()
    assert _answers(tg.http) == ["AAPL: order in progress."]
    assert tg.http.texts() == []
    assert tg.status() == "PENDING"


def test_tap_approve_unexpected_error_is_answered_not_redelivered(tg, monkeypatch):
    def locked(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(tg.trades, "fill", locked)
    r = tg.tap()
    assert r.status_code == 200 and r.json()["status"] == "PENDING"
    assert _answers(tg.http) == ["Broker error."]
    assert "AAPL not bought: Broker error." in tg.http.texts()
    assert all(o["status"] != "FILLED" for o in tg.trades.list_orders())
    assert tg.status() == "PENDING"
