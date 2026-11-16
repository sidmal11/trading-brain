"""The poll handler end to end, with the market scan and LLM mocked.

Covers the one path the use-case tests skip: lambda_handler itself, from scan
results to the stored alert and the Telegram copies. Uses the SQLite backend in a
temp file, so no AWS and no real network calls.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lambda_function  # noqa: E402
from config import Config  # noqa: E402
from utils import telegram_client as tc  # noqa: E402
from utils.storage import alert_store, subscriber_store  # noqa: E402

ADMIN = "1000"
SUB = "2000"


class FakeScan:
    def get_market_scan_results(self):
        return [{"ticker": "NVDA", "volume_score": 9.0}, {"ticker": "LOWVOL", "volume_score": 0.1}]

    def get_stock_details(self, ticker):
        return {"ticker": ticker}


class FakeLlm:
    def analyze_stock(self, ticker, signal_desc, stock_data):
        return f"Pros: strong volume. Cons: mock analysis for {ticker}."


class Telegram:
    """Records every Bot API call; returns a message id per send."""

    def __init__(self):
        self.calls = []
        self._id = 500

    def __call__(self, url, json=None, timeout=None):
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, json or {}))
        self._id += 1
        recorder = self

        class Response:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True, "result": {"message_id": recorder._id}}

        return Response()

    def sends(self):
        return [p for m, p in self.calls if m == "sendMessage"]


@pytest.fixture
def poll(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setattr(Config, "STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(Config, "SQLITE_PATH", str(tmp_path / "poll.db"))
    monkeypatch.setattr(Config, "TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", ADMIN)
    monkeypatch.setattr(Config, "ADMIN_CHAT_IDS", "")
    monkeypatch.setattr(Config, "STRATEGY_VOLUME_THRESHOLD", 1.0)

    monkeypatch.setattr(lambda_function, "ApiClient", FakeScan)
    monkeypatch.setattr(lambda_function, "LlmClient", FakeLlm)
    http = Telegram()
    monkeypatch.setattr(tc.requests, "post", http)

    subscriber_store().subscribe(SUB)
    return http


def test_poll_stores_only_signals_above_threshold(poll):
    result = lambda_function.lambda_handler({}, None)
    assert result["body"] == "Processed 1 signals."
    tickers = [a["ticker"] for a in alert_store().list_alerts()]
    assert tickers == ["NVDA"]


def test_poll_saves_pending_alert_with_admin_message_id(poll):
    lambda_function.lambda_handler({}, None)
    alert = alert_store().list_alerts()[0]
    assert alert["status"] == "PENDING"
    assert alert["telegramChatId"] == ADMIN
    assert alert["telegramMessageId"]  # the admin copy is linked for later edits
    assert "mock analysis for NVDA" in alert["aiProsCons"]


def test_poll_sends_admin_copy_with_buttons_and_subscriber_copy_without(poll):
    lambda_function.lambda_handler({}, None)
    sends = poll.sends()
    to_admin = [p for p in sends if str(p["chat_id"]) == ADMIN]
    to_sub = [p for p in sends if str(p["chat_id"]) == SUB]
    assert to_admin and "reply_markup" in to_admin[0]
    assert to_sub and "reply_markup" not in to_sub[0]


def test_poll_with_no_scan_results_sends_nothing(poll, monkeypatch):
    monkeypatch.setattr(FakeScan, "get_market_scan_results", lambda self: [])
    result = lambda_function.lambda_handler({}, None)
    assert result["body"] == "No signals identified."
    assert poll.sends() == []
