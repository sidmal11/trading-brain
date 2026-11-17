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
    last_kwargs: dict = {}

    def get_market_scan_results(self, market="US", session_fraction=1.0, trading_date=None):
        FakeScan.last_kwargs = {"session_fraction": session_fraction, "trading_date": trading_date}
        return [
            {"ticker": "NVDA", "volume_score": 9.0, "price": 182.4},
            {"ticker": "LOWVOL", "volume_score": 0.1, "price": 10.0},
        ]

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

    monkeypatch.setattr(lambda_function, "session_fraction", lambda: 0.5)
    monkeypatch.setattr(lambda_function, "trading_date", lambda: "2026-11-16")
    monkeypatch.setattr(lambda_function, "ApiClient", FakeScan)
    monkeypatch.setattr(lambda_function, "LlmClient", FakeLlm)
    http = Telegram()
    monkeypatch.setattr(tc.requests, "post", http)

    subscriber_store().subscribe(SUB)
    return http


def test_poll_passes_clock_values_to_the_scan(poll):
    lambda_function.lambda_handler({}, None)
    assert FakeScan.last_kwargs == {"session_fraction": 0.5, "trading_date": "2026-11-16"}


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
    monkeypatch.setattr(FakeScan, "get_market_scan_results", lambda self, **k: [])
    result = lambda_function.lambda_handler({}, None)
    assert result["body"] == "No signals identified."
    assert poll.sends() == []


def test_second_poll_same_day_does_not_alert_again(poll):
    lambda_function.lambda_handler({}, None)
    sends_after_first = len(poll.sends())
    result = lambda_function.lambda_handler({}, None)
    assert result["body"] == "Processed 0 signals."
    assert len(poll.sends()) == sends_after_first
    assert len(alert_store().list_alerts()) == 1


def test_next_trading_day_alerts_again(poll, monkeypatch):
    lambda_function.lambda_handler({}, None)
    monkeypatch.setattr(lambda_function, "trading_date", lambda: "2026-11-17")
    lambda_function.lambda_handler({}, None)
    assert len(alert_store().list_alerts()) == 2


def test_already_alerted_ticker_does_not_call_the_llm(poll, monkeypatch):
    lambda_function.lambda_handler({}, None)

    def boom(self, *a, **k):
        raise AssertionError("LLM called for a deduped ticker")

    monkeypatch.setattr(FakeLlm, "analyze_stock", boom)
    lambda_function.lambda_handler({}, None)


def test_market_closed_skips_the_scan(poll, monkeypatch):
    monkeypatch.setattr(lambda_function, "session_fraction", lambda: None)

    def boom(self, *a, **k):
        raise AssertionError("scan ran while the market was closed")

    monkeypatch.setattr(FakeScan, "get_market_scan_results", boom)
    result = lambda_function.lambda_handler({}, None)
    assert result["body"] == "Market closed; skipping scan."
    assert poll.sends() == []


def test_first_thirty_minutes_skip_the_scan(poll, monkeypatch):
    monkeypatch.setattr(lambda_function, "session_fraction", lambda: 10 / 390)
    result = lambda_function.lambda_handler({}, None)
    assert result["body"] == "Too early in the session; skipping scan."
    assert alert_store().list_alerts() == []


def test_alert_records_price_strategy_and_date(poll):
    lambda_function.lambda_handler({}, None)
    alert = alert_store().list_alerts()[0]
    assert alert["priceAtAlert"] == 182.4
    assert alert["strategy"] == "volume"
    assert alert["tradingDate"] == "2026-11-16"


def test_deduped_tickers_do_not_use_up_the_run_allowance(poll, monkeypatch):
    from utils import settings_store

    base = settings_store.defaults()
    monkeypatch.setattr(lambda_function, "effective_settings",
                        lambda: {**base, "maxCandidatesPerRun": 1, "volumeThreshold": 1.0})
    lambda_function.lambda_handler({}, None)  # alerts NVDA
    monkeypatch.setattr(FakeScan, "get_market_scan_results", lambda self, **k: [
        {"ticker": "NVDA", "volume_score": 9.0, "price": 182.4},
        {"ticker": "AMD", "volume_score": 5.0, "price": 120.0},
    ])
    lambda_function.lambda_handler({}, None)
    assert sorted(a["ticker"] for a in alert_store().list_alerts()) == ["AMD", "NVDA"]


def _trading(monkeypatch, on, dollars=500):
    from utils import settings_store

    base = settings_store.defaults()
    monkeypatch.setattr(lambda_function, "effective_settings",
                        lambda: {**base, "volumeThreshold": 1.0, "tradingEnabled": on, "dollarsPerTrade": dollars})


def test_order_line_only_on_admin_copy(poll, monkeypatch):
    _trading(monkeypatch, True)
    lambda_function.lambda_handler({}, None)
    sends = poll.sends()
    admin = [p for p in sends if str(p["chat_id"]) == ADMIN][0]["text"]
    sub = [p for p in sends if str(p["chat_id"]) == SUB][0]["text"]
    assert "Approve buys 2 NVDA at about $182.40, about $365 (MOCK)." in admin
    assert "Approve buys" not in sub


def test_order_line_when_below_one_share(poll, monkeypatch):
    _trading(monkeypatch, True, dollars=100)
    lambda_function.lambda_handler({}, None)
    admin = [p for p in poll.sends() if str(p["chat_id"]) == ADMIN][0]["text"]
    assert "Approve will not buy: $100 is below one share at $182.40 (MOCK)." in admin


def test_no_order_line_when_trading_off(poll, monkeypatch):
    _trading(monkeypatch, False)
    lambda_function.lambda_handler({}, None)
    assert not any("Approve buys" in p["text"] or "MOCK" in p["text"] for p in poll.sends())
