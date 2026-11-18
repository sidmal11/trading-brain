"""End-to-end use cases, runnable locally with no AWS account and no real Telegram.

DynamoDB is mocked in-process with moto; Telegram's HTTP layer is replaced by a
recorder, so nothing is sent to a real chat. Each test is one use case from
docs/USE_CASES.md (UC-numbers in the names).

Run:  python -m pytest tests/test_e2e_local.py -v
"""

import contextlib
import sys
import uuid
from pathlib import Path

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import main as api_main  # noqa: E402
from config import Config  # noqa: E402
from utils import telegram_client as tc  # noqa: E402
from utils.storage import alert_store, subscriber_store  # noqa: E402

ADMIN = "1000"
SUB = "2000"
SUB_2 = "3000"
SECRET = "test-secret"
WEBHOOK = "/api/v1/telegram/webhook"
HEADERS = {"X-Telegram-Bot-Api-Secret-Token": SECRET}


class FakeTelegramHTTP:
    """Records every Bot API call. `blocked` chats answer 403, like a user who blocked the bot."""

    def __init__(self, blocked=()):
        self.blocked = set(str(c) for c in blocked)
        self.calls = []  # (method, payload)
        self._next_message_id = 100

    def __call__(self, url, json=None, timeout=None):
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, json or {}))
        chat = str((json or {}).get("chat_id", ""))

        class Response:
            status_code = 403 if chat in self.blocked else 200

            def raise_for_status(self_inner):
                if self_inner.status_code >= 400:
                    import requests
                    raise requests.HTTPError(f"HTTP {self_inner.status_code}")

            def json(self_inner):
                self._next_message_id += 1
                return {"ok": True, "result": {"message_id": self._next_message_id}}

        return Response()

    def sent_to(self, chat_id):
        return [p for m, p in self.calls if m == "sendMessage" and str(p.get("chat_id")) == str(chat_id)]


@pytest.fixture(params=["dynamodb", "sqlite"])
def world(request, monkeypatch, tmp_path):
    """Every use-case test runs once per storage backend, against the same behaviour."""
    backend = request.param
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setattr(Config, "STORAGE_BACKEND", backend)
    monkeypatch.setattr(Config, "SQLITE_PATH", str(tmp_path / "trading.db"))
    monkeypatch.setattr(Config, "TRADES_SQLITE_PATH", str(tmp_path / "trades.db"))

    ctx = mock_aws() if backend == "dynamodb" else contextlib.nullcontext()
    with ctx:
        if backend == "dynamodb":
            ddb = boto3.resource("dynamodb", region_name="us-east-1")
            ddb.create_table(TableName="TradingAlerts", KeySchema=[{"AttributeName": "alertId", "KeyType": "HASH"}],
                             AttributeDefinitions=[{"AttributeName": "alertId", "AttributeType": "S"}],
                             BillingMode="PAY_PER_REQUEST")
            ddb.create_table(TableName="TelegramSubscribers", KeySchema=[{"AttributeName": "chatId", "KeyType": "HASH"}],
                             AttributeDefinitions=[{"AttributeName": "chatId", "AttributeType": "S"}],
                             BillingMode="PAY_PER_REQUEST")

        monkeypatch.setattr(Config, "TELEGRAM_BOT_TOKEN", "123:fake")
        monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", ADMIN)
        monkeypatch.setattr(Config, "ADMIN_CHAT_IDS", "")
        monkeypatch.setattr(Config, "TELEGRAM_WEBHOOK_SECRET", SECRET)
        monkeypatch.setattr(Config, "DYNAMODB_TABLE_NAME", "TradingAlerts")
        monkeypatch.setattr(Config, "TELEGRAM_SUBSCRIBERS_TABLE", "TelegramSubscribers")
        monkeypatch.setattr(Config, "ALLOW_UNAUTHENTICATED", True)
        monkeypatch.setattr(api_main, "_db_client", None)
        monkeypatch.setattr(api_main, "_subscriber_store", None)

        http = FakeTelegramHTTP()
        monkeypatch.setattr(tc.requests, "post", http)

        yield {"http": http, "client": TestClient(api_main.create_app()),
               "db": alert_store(), "subs": subscriber_store(), "backend": backend}


def _message(text, chat_id, chat_type="private"):
    return {"message": {"text": text, "chat": {"id": int(chat_id), "type": chat_type, "first_name": "T"}}}


def _tap(data, from_id):
    return {"callback_query": {"id": "cb", "data": data, "from": {"id": int(from_id)},
                               "message": {"message_id": 1, "chat": {"id": int(from_id)}}}}


def _broadcast(world, ticker="NVDA"):
    """What the poll Lambda does for one signal: alert everyone, persist the alert."""
    alert_id = str(uuid.uuid4())
    recipients = list(dict.fromkeys(sorted(tc.TelegramClient().admin_chat_ids) + world["subs"].active_chat_ids()))
    message_id, blocked, _ = tc.TelegramClient().broadcast_alert(ticker, "vol surge", "pros/cons", alert_id, recipients)
    for chat in blocked:
        world["subs"].deactivate(chat)
    world["db"].save_alert(alert_id=alert_id, ticker=ticker, signal_details="vol surge",
                           pros_cons="pros/cons", chat_id=ADMIN, message_id=str(message_id))
    return alert_id


def test_alert_records_price_and_is_found_by_ticker_strategy_date(world):
    db = world["db"]
    db.save_alert("a-price", "NVDA", "sig", "pros", price=182.4, trading_date="2026-11-16")
    assert db.alert_exists("NVDA", "volume", "2026-11-16") is True
    assert db.alert_exists("NVDA", "volume", "2026-11-17") is False
    assert float(db.get_alert("a-price")["priceAtAlert"]) == 182.4


def test_alert_round_trips_strategy_fields(world):
    db = world["db"]
    items = [{"key": "uptrend", "label": "Uptrend", "passed": True, "points": 25, "detail": "close 110.5"},
             {"key": "macd", "label": "MACD", "passed": False, "points": 25, "detail": "no cross"}]
    db.save_alert("a-macd", "AAPL", "sig", "pros", price=101.25, strategy="macd", trading_date="2026-11-16",
                  signal_type="MACD_CROSSOVER", confidence=25, checklist=items, entry=101.25, stop=95.5, target=110.88)
    a = db.get_alert("a-macd")
    assert a["signalType"] == "MACD_CROSSOVER" and int(a["confidence"]) == 25
    assert [dict(x, points=int(x["points"])) for x in a["checklist"]] == items
    assert [float(a[k]) for k in ("entryPrice", "stopPrice", "targetPrice")] == [101.25, 95.5, 110.88]
    db.save_alert("a-old", "NVDA", "sig", "pros")
    old = db.get_alert("a-old")
    assert old["signalType"] == "VOLUME_BREAKTHROUGH"
    assert not any(k in old for k in ("confidence", "checklist", "entryPrice", "stopPrice", "targetPrice"))


# ── UC-1 Subscribe ────────────────────────────────────────────────────────────

def test_uc1_start_subscribes_and_replies(world):
    world["client"].post(WEBHOOK, json=_message("/start", SUB), headers=HEADERS)
    assert world["subs"].active_chat_ids() == [SUB]
    assert world["http"].sent_to(SUB)  # confirmation message sent


def test_uc1_start_twice_is_harmless(world):
    for _ in range(2):
        world["client"].post(WEBHOOK, json=_message("/start", SUB), headers=HEADERS)
    assert world["subs"].active_chat_ids() == [SUB]


# ── UC-2 Unsubscribe ──────────────────────────────────────────────────────────

def test_uc2_stop_stops_alerts(world):
    world["client"].post(WEBHOOK, json=_message("/start", SUB), headers=HEADERS)
    world["client"].post(WEBHOOK, json=_message("/stop", SUB), headers=HEADERS)
    assert world["subs"].active_chat_ids() == []


def test_uc2_resubscribe_after_stop(world):
    world["client"].post(WEBHOOK, json=_message("/start", SUB), headers=HEADERS)
    world["client"].post(WEBHOOK, json=_message("/stop", SUB), headers=HEADERS)
    world["client"].post(WEBHOOK, json=_message("/start", SUB), headers=HEADERS)
    assert world["subs"].active_chat_ids() == [SUB]


# ── UC-3 Alert fan-out ────────────────────────────────────────────────────────

def test_uc3_every_subscriber_receives_the_alert(world):
    world["client"].post(WEBHOOK, json=_message("/start", SUB), headers=HEADERS)
    world["client"].post(WEBHOOK, json=_message("/start", SUB_2), headers=HEADERS)
    _broadcast(world)
    assert world["http"].sent_to(SUB) and world["http"].sent_to(SUB_2)
    assert world["http"].sent_to(ADMIN)


def test_uc3_only_admin_copy_has_buttons(world):
    world["client"].post(WEBHOOK, json=_message("/start", SUB), headers=HEADERS)
    _broadcast(world)
    assert "reply_markup" in world["http"].sent_to(ADMIN)[0]
    assert "reply_markup" not in world["http"].sent_to(SUB)[-1]


def test_uc3_stopped_subscriber_gets_nothing(world):
    world["client"].post(WEBHOOK, json=_message("/start", SUB), headers=HEADERS)
    world["client"].post(WEBHOOK, json=_message("/stop", SUB), headers=HEADERS)
    _broadcast(world)
    # Only the /stop confirmation may reach the chat, never the alert.
    assert all("NVDA" not in p["text"] for p in world["http"].sent_to(SUB))


# ── UC-4 Blocked bot ──────────────────────────────────────────────────────────

def test_uc4_user_who_blocked_the_bot_is_deactivated(world, monkeypatch):
    world["client"].post(WEBHOOK, json=_message("/start", SUB), headers=HEADERS)
    world["http"].blocked.add(SUB)
    _broadcast(world)
    assert world["subs"].active_chat_ids() == []


def test_uc4_other_failures_keep_the_subscriber(world):
    world["client"].post(WEBHOOK, json=_message("/start", SUB), headers=HEADERS)
    original = world["http"].__call__

    def flaky(url, json=None, timeout=None):
        if str((json or {}).get("chat_id")) == SUB:
            class Boom:
                status_code = 500

                def raise_for_status(self):
                    import requests
                    raise requests.HTTPError("HTTP 500")

                def json(self):
                    return {}
            return Boom()
        return original(url, json=json, timeout=timeout)

    tc.requests.post = flaky
    _broadcast(world)
    assert world["subs"].active_chat_ids() == [SUB]


# ── UC-5 Admin approves / rejects ─────────────────────────────────────────────

def test_uc5_admin_approve_is_recorded(world):
    alert_id = _broadcast(world)
    world["client"].post(WEBHOOK, json=_tap(f"approve:{alert_id}:NVDA", ADMIN), headers=HEADERS)
    assert world["db"].get_alert(alert_id)["status"] == "APPROVED"


def test_uc5_admin_reject_is_recorded(world):
    alert_id = _broadcast(world)
    world["client"].post(WEBHOOK, json=_tap(f"reject:{alert_id}:NVDA", ADMIN), headers=HEADERS)
    assert world["db"].get_alert(alert_id)["status"] == "REJECTED"


def test_uc5_decision_strips_buttons_from_the_admin_message(world):
    alert_id = _broadcast(world)
    world["client"].post(WEBHOOK, json=_tap(f"approve:{alert_id}:NVDA", ADMIN), headers=HEADERS)
    edits = [p for m, p in world["http"].calls if m == "editMessageReplyMarkup"]
    assert edits and str(edits[0]["chat_id"]) == ADMIN


# ── UC-6 Subscriber cannot decide ─────────────────────────────────────────────

def test_uc6_subscriber_tap_does_not_change_status(world):
    world["client"].post(WEBHOOK, json=_message("/start", SUB), headers=HEADERS)
    alert_id = _broadcast(world)
    world["client"].post(WEBHOOK, json=_tap(f"approve:{alert_id}:NVDA", SUB), headers=HEADERS)
    assert world["db"].get_alert(alert_id)["status"] == "PENDING"


# ── UC-7 Security ─────────────────────────────────────────────────────────────

def test_uc7_webhook_rejects_missing_secret(world):
    r = world["client"].post(WEBHOOK, json=_message("/start", SUB), headers={"X-Telegram-Bot-Api-Secret-Token": "no"})
    assert r.status_code == 401
    assert world["subs"].active_chat_ids() == []


def test_uc7_commands_in_groups_are_ignored(world):
    world["client"].post(WEBHOOK, json=_message("/start", -100, chat_type="group"), headers=HEADERS)
    assert world["subs"].active_chat_ids() == []


def test_uc7_unknown_alert_tap_is_harmless(world):
    r = world["client"].post(WEBHOOK, json=_tap("approve:does-not-exist:NVDA", ADMIN), headers=HEADERS)
    assert r.status_code == 200
    assert r.json().get("ignored") == "unknown alert"


# ── Expired requests are final ────────────────────────────────────────────────

def _expired(world):
    alert_id = _broadcast(world)
    world["db"].expire_alert(alert_id, "Cleared at upgrade.")
    return alert_id


@pytest.mark.parametrize("decision", ["REJECTED", "PENDING", "APPROVED"])
def test_dashboard_decision_on_an_expired_alert_is_refused(world, decision):
    alert_id = _expired(world)
    before = len(world["http"].calls)
    r = world["client"].post(f"/api/v1/alerts/{alert_id}/review", json={"decision": decision})
    assert r.status_code == 409 and r.json()["detail"] == "Request expired: Cleared at upgrade."
    assert world["db"].get_alert(alert_id)["status"] == "EXPIRED"
    assert len(world["http"].calls) == before  # no Telegram edit


def test_store_refuses_to_change_an_expired_alert(world):
    alert_id = _expired(world)
    assert world["db"].update_alert_status(alert_id, "PENDING") is False
    assert world["db"].get_alert(alert_id)["status"] == "EXPIRED"


def test_telegram_reject_tap_on_an_expired_alert_changes_nothing(world):
    alert_id = _expired(world)
    world["client"].post(WEBHOOK, json=_tap(f"reject:{alert_id}:NVDA", ADMIN), headers=HEADERS)
    assert world["db"].get_alert(alert_id)["status"] == "EXPIRED"
    answers = [p for m, p in world["http"].calls if m == "answerCallbackQuery"]
    assert answers and answers[-1]["text"] == "Request expired: Cleared at upgrade."
