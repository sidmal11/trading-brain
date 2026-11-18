"""Tests for alert delivery and the approval callback payload.

`lambda_function.py` imported `TelegramClient` from this module while the class
did not exist, so the poll handler died at import. These cover the class that
closed that gap, and in particular the callback parsing, because a misread
payload would record a trade decision the trader never made.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Config  # noqa: E402
from utils.telegram_client import (  # noqa: E402
    MAX_MESSAGE_CHARS,
    TelegramClient,
    build_approval_keyboard,
    parse_callback,
)
from utils import telegram_client as tc  # noqa: E402


# ── Callback payloads ─────────────────────────────────────────────────────────

def test_keyboard_carries_the_alert_id_in_both_buttons():
    keyboard = build_approval_keyboard("alert-1", "NVDA")
    buttons = keyboard["inline_keyboard"][0]

    assert buttons[0]["callback_data"] == "approve:alert-1:NVDA"
    assert buttons[1]["callback_data"] == "reject:alert-1:NVDA"


def test_round_trip_from_keyboard_to_decision():
    keyboard = build_approval_keyboard("alert-1", "NVDA")
    for button, expected in zip(keyboard["inline_keyboard"][0], ["APPROVED", "REJECTED"]):
        assert parse_callback(button["callback_data"]) == ("alert-1", "NVDA", expected)


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "garbage",
        "approve",
        "approve:alert-1",
        "delete:alert-1:NVDA",   # unknown verb
        "approve::NVDA",         # no alert id
    ],
)
def test_unparseable_payloads_return_none(payload):
    assert parse_callback(payload) is None


def test_ticker_containing_a_colon_is_preserved():
    # split(":", 2) caps the split, so a ticker like BRK:B survives intact
    # rather than being truncated to "BRK".
    assert parse_callback("approve:alert-1:BRK:B") == ("alert-1", "BRK:B", "APPROVED")


# ── Message formatting ────────────────────────────────────────────────────────

def test_html_special_characters_are_escaped():
    body = TelegramClient(token="t", chat_id="c").format_alert(
        "A&B", "price < 100", "pros > cons"
    )
    assert "&amp;" in body and "&lt;" in body and "&gt;" in body


def test_markdown_characters_in_llm_output_are_left_alone():
    # HTML parse mode is used precisely so unbalanced * and _ from the model
    # cannot make Telegram reject the whole message.
    body = TelegramClient(token="t", chat_id="c").format_alert("T", "sig", "a *b _c `d")
    assert "a *b _c `d" in body


def test_long_analysis_is_truncated_to_the_api_limit():
    body = TelegramClient(token="t", chat_id="c").format_alert("T", "sig", "x" * 9000)
    assert len(body) == MAX_MESSAGE_CHARS
    assert body.endswith("(analysis truncated)</i>")


# ── Configuration guards ──────────────────────────────────────────────────────

def test_unconfigured_client_skips_sending_instead_of_raising():
    client = TelegramClient(token="", chat_id="")
    assert client.is_configured is False
    # A missing token is a deployment gap, not a reason to abort a scan that
    # already paid for its LLM analysis.
    assert client.send_alert("NVDA", "sig", "analysis", alert_id="a-1") is None


def test_send_attaches_buttons_when_an_alert_id_is_given():
    client = TelegramClient(token="t", chat_id="c")
    captured = {}

    def fake_post(method, payload):
        captured["method"] = method
        captured["payload"] = payload
        return {"ok": True, "result": {"message_id": 4242}}

    client._post = fake_post
    message_id = client.send_alert("NVDA", "sig", "analysis", alert_id="alert-1")

    assert message_id == 4242
    assert captured["method"] == "sendMessage"
    assert captured["payload"]["parse_mode"] == "HTML"
    assert "reply_markup" in captured["payload"]


def test_send_without_an_alert_id_omits_the_buttons():
    client = TelegramClient(token="t", chat_id="c")
    captured = {}

    client._post = lambda method, payload: captured.update(payload) or {
        "ok": True, "result": {"message_id": 1}
    }
    client.send_alert("NVDA", "sig", "analysis")

    # A tap could not be matched back to a row, so no buttons are offered.
    assert "reply_markup" not in captured


def test_failed_send_returns_none():
    client = TelegramClient(token="t", chat_id="c")
    client._post = lambda method, payload: None
    assert client.send_alert("NVDA", "sig", "analysis", alert_id="a-1") is None


def test_api_error_response_returns_none():
    client = TelegramClient(token="t", chat_id="c")
    client._post = lambda method, payload: {"ok": False, "description": "chat not found"}
    assert client.send_alert("NVDA", "sig", "analysis", alert_id="a-1") is None


def test_order_line_survives_a_long_analysis(monkeypatch):
    monkeypatch.setattr(Config, "ADMIN_CHAT_IDS", "")
    client = TelegramClient(token="t", chat_id="c")
    line = "Approve buys 2 AAPL (MOCK)."
    text = client._alert_payload("c", "AAPL", "sig", "x" * 5000, "a1", line)["text"]
    assert len(text) <= MAX_MESSAGE_CHARS
    assert text.endswith(line) and "(analysis truncated)" in text


# ── Expiry clean-up ───────────────────────────────────────────────────────────

def _ago(hours):
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _recording_client(monkeypatch, ok=True):
    client = TelegramClient(token="t", chat_id="1")
    calls = []
    monkeypatch.setattr(client, "_post", lambda m, p: calls.append((m, p)) or {"ok": ok})
    return client, calls


def test_delete_message_reports_success(monkeypatch):
    client, calls = _recording_client(monkeypatch)
    assert client.delete_message("1", 5) is True
    assert calls == [("deleteMessage", {"chat_id": "1", "message_id": 5})]


def test_recent_copy_is_deleted(monkeypatch):
    client, calls = _recording_client(monkeypatch)
    client.expire_message("2000", 7, _ago(2), "NVDA request expired: x")
    assert [m for m, _ in calls] == ["deleteMessage"]


def test_copy_older_than_48_hours_is_edited_to_the_reason_without_buttons(monkeypatch):
    client, calls = _recording_client(monkeypatch)
    client.expire_message("2000", 7, _ago(50), "NVDA request expired: x")
    assert calls == [("editMessageText", {"chat_id": "2000", "message_id": 7, "text": "NVDA request expired: x"})]


def test_failed_delete_falls_back_to_an_edit(monkeypatch):
    client, calls = _recording_client(monkeypatch, ok=False)
    assert client.expire_message("2000", 7, _ago(47.99), "r") is False  # the edit failed too
    assert [m for m, _ in calls] == ["deleteMessage", "editMessageText"]


def test_expire_message_reports_success(monkeypatch):
    client, _ = _recording_client(monkeypatch)
    assert client.expire_message("2000", 7, _ago(2), "r") is True
    assert client.expire_message("2000", 7, _ago(50), "r") is True


def test_broadcast_returns_every_recipient_message(monkeypatch):
    monkeypatch.setattr(Config, "ADMIN_CHAT_IDS", "")
    client = TelegramClient(token="t", chat_id="1")
    ids = iter([11, 12])
    monkeypatch.setattr(client, "_post", lambda m, p: {"ok": True, "result": {"message_id": next(ids)}})
    admin_id, blocked, sent = client.broadcast_alert("NVDA", "vol", "a", "alert-1", ["1", "555"])
    assert admin_id == 11 and blocked == []
    assert [(s["chatId"], s["messageId"]) for s in sent] == [("1", 11), ("555", 12)]
    assert all(s["sentAt"].endswith("+00:00") for s in sent)


# ── Amount buttons and typed amounts (phase 1 task 4) ─────────────────────────

def test_amount_keyboard_callbacks_fit_telegrams_64_bytes():
    alert_id = "123e4567-e89b-12d3-a456-426614174000"
    kb = tc.build_amount_keyboard(alert_id, "GOOGL", 123456.123456, 10000)
    data = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
    assert data == [f"bq:{alert_id}:123456.123456", f"bd:{alert_id}:10000", f"ba:{alert_id}", f"reject:{alert_id}:GOOGL"]
    assert [b["text"] for row in kb["inline_keyboard"] for b in row][:2] == ["Buy 123456.123456 sh", "Buy $10000"]
    assert all(len(d.encode()) <= 64 for d in data)
    confirm = tc.build_confirm_keyboard(alert_id, None, 9999999.99)
    assert all(len(b["callback_data"].encode()) <= 64 for row in confirm["inline_keyboard"] for b in row)


@pytest.mark.parametrize("data, expected", [
    ("bq:a1:0.25", ("buy", "a1", 0.25, None)),
    ("bd:a1:10", ("buy", "a1", None, 10.0)),
    ("bc:a1:s3", ("buy", "a1", 3.0, None)),
    ("bc:a1:d10.5", ("buy", "a1", None, 10.5)),
    ("ba:a1", ("ask", "a1", None, None)),
    ("bx:a1", ("cancel", "a1", None, None)),
    ("bq:a1:0", None), ("bq:a1:-1", None), ("bq:a1:nan", None), ("bq:a1:inf", None),
    ("bq:a1:1000001", None), ("bd:a1:10000001", None), ("bc:a1:x3", None), ("bq::1", None), ("zz:a1", None),
    ("approve:a1:AAPL", None),
])
def test_parse_amount_callback(data, expected):
    assert tc.parse_amount_callback(data) == expected


@pytest.mark.parametrize("text, expected", [
    ("5", (5.0, None)), ("0.25", (0.25, None)), (" $10 ", (None, 10.0)), ("10$", (None, 10.0)),
    ("$ 2.5", (None, 2.5)), ("$10$", None), ("ten", None), ("", None), ("0", None), ("-3", None), ("1e3", None),
])
def test_parse_amount_text(text, expected):
    assert tc.parse_amount_text(text) == expected
