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

from utils.telegram_client import (  # noqa: E402
    MAX_MESSAGE_CHARS,
    TelegramClient,
    build_approval_keyboard,
    parse_callback,
)


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
