"""Subscriber storage and per-user alert broadcast.

The bug these guard against: sending every alert to one configured chat, or
letting a subscriber's copy carry Approve/Reject buttons. Either one would let
someone who never signed up for trade decisions make them.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Config  # noqa: E402
from utils.subscriber_store import SubscriberStore  # noqa: E402
from utils.telegram_client import TelegramClient  # noqa: E402


class FakeTable:
    """The three DynamoDB calls SubscriberStore makes, over a dict."""

    def __init__(self):
        self.items = {}

    def put_item(self, Item):
        self.items[Item["chatId"]] = dict(Item)

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues):
        self.items[Key["chatId"]]["active"] = ExpressionAttributeValues[":off"]

    def scan(self, FilterExpression, ExpressionAttributeValues, **_):
        wanted = ExpressionAttributeValues[":on"]
        return {"Items": [i for i in self.items.values() if i.get("active") is wanted]}


@pytest.fixture
def store():
    return SubscriberStore(table=FakeTable())


# ── Subscriber store ──────────────────────────────────────────────────────────

def test_subscribe_then_list_returns_the_chat(store):
    store.subscribe(111, username="a")
    assert store.active_chat_ids() == ["111"]


def test_deactivate_removes_the_chat_from_the_active_list(store):
    store.subscribe(111)
    store.subscribe(222)
    store.deactivate(111)
    assert store.active_chat_ids() == ["222"]


def test_resubscribing_reactivates(store):
    store.subscribe(111)
    store.deactivate(111)
    store.subscribe(111)
    assert store.active_chat_ids() == ["111"]


# ── Broadcast ─────────────────────────────────────────────────────────────────

class RecordingPost:
    """Stands in for TelegramClient._post; answers per chat id."""

    def __init__(self, status_by_chat=None):
        self.status_by_chat = status_by_chat or {}
        self.sent = []  # (chat_id, payload)

    def __call__(self, method, payload):
        chat_id = str(payload["chat_id"])
        self.sent.append((chat_id, payload))
        status = self.status_by_chat.get(chat_id)
        if status:
            return {"ok": False, "status": status}
        return {"ok": True, "result": {"message_id": 1000 + len(self.sent)}}


@pytest.fixture
def client_for_broadcast(monkeypatch):
    monkeypatch.setattr(Config, "ADMIN_CHAT_IDS", "")
    monkeypatch.setattr(Config, "TELEGRAM_BOT_TOKEN", "token")
    return TelegramClient(token="token", chat_id="1")


def test_only_admin_copies_carry_approve_reject_buttons(client_for_broadcast, monkeypatch):
    post = RecordingPost()
    monkeypatch.setattr(client_for_broadcast, "_post", post)

    client_for_broadcast.broadcast_alert("NVDA", "vol", "analysis", "alert-1", ["1", "555"])

    by_chat = {chat: payload for chat, payload in post.sent}
    assert "reply_markup" in by_chat["1"]
    assert "reply_markup" not in by_chat["555"]


def test_admin_message_id_is_returned_for_storage(client_for_broadcast, monkeypatch):
    post = RecordingPost()
    monkeypatch.setattr(client_for_broadcast, "_post", post)

    message_id, _, _ = client_for_broadcast.broadcast_alert("NVDA", "vol", "analysis", "alert-1", ["1", "555"])

    assert message_id == 1001  # the first send, to the admin chat


def test_a_blocked_subscriber_is_reported_not_retried(client_for_broadcast, monkeypatch):
    post = RecordingPost(status_by_chat={"555": 403})
    monkeypatch.setattr(client_for_broadcast, "_post", post)

    message_id, blocked, _ = client_for_broadcast.broadcast_alert("NVDA", "vol", "analysis", "alert-1", ["1", "555"])

    assert blocked == ["555"]
    assert message_id == 1001  # one blocked subscriber does not lose the admin copy


def test_other_errors_are_not_reported_as_blocked(client_for_broadcast, monkeypatch):
    post = RecordingPost(status_by_chat={"555": 500})
    monkeypatch.setattr(client_for_broadcast, "_post", post)

    _, blocked, _ = client_for_broadcast.broadcast_alert("NVDA", "vol", "analysis", "alert-1", ["1", "555"])

    # A 500 is not the user's doing, so their subscription must not be dropped.
    assert blocked == []


def test_admin_list_from_config_also_gets_buttons(monkeypatch):
    monkeypatch.setattr(Config, "ADMIN_CHAT_IDS", "1, 777")
    client = TelegramClient(token="token", chat_id="1")
    post = RecordingPost()
    monkeypatch.setattr(client, "_post", post)

    client.broadcast_alert("NVDA", "vol", "analysis", "alert-1", ["777"])

    assert "reply_markup" in post.sent[0][1]
