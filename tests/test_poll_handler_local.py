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
    calls: list = []

    def analyze_stock(self, ticker, signal_desc, stock_data, checklist=None, strategy_notes=""):
        FakeLlm.calls.append({"ticker": ticker, "checklist": checklist, "notes": strategy_notes})
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
    monkeypatch.setattr(Config, "TRADES_SQLITE_PATH", str(tmp_path / "trades.db"))
    monkeypatch.setattr(Config, "TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", ADMIN)
    monkeypatch.setattr(Config, "ADMIN_CHAT_IDS", "")
    monkeypatch.setattr(Config, "STRATEGY_VOLUME_THRESHOLD", 1.0)

    monkeypatch.setattr(lambda_function, "session_fraction", lambda: 0.5)
    monkeypatch.setattr(lambda_function, "trading_date", lambda: "2026-11-16")
    monkeypatch.setattr(lambda_function, "ApiClient", FakeScan)
    monkeypatch.setattr(lambda_function, "LlmClient", FakeLlm)
    monkeypatch.setattr(lambda_function, "completed_bars", lambda tickers, today: {})
    monkeypatch.setattr(lambda_function, "_macd_days", set())
    monkeypatch.setattr(FakeLlm, "calls", [])
    monkeypatch.setattr(lambda_function, "refresh_daily", lambda *a, **k: {"failed": False})  # no Yahoo
    monkeypatch.setattr(lambda_function, "_bar_days", set())
    monkeypatch.setattr(lambda_function, "_backtest_days", set())
    monkeypatch.setattr(lambda_function, "_bar_retry_at", 0.0)
    from utils.settings_store import SettingsStore
    SettingsStore(Config.SQLITE_PATH).put({"requestStrategies": ["macd", "volume"]})  # the old behaviour
    monkeypatch.setattr(lambda_function, "download_earnings", lambda tickers, today: {})  # no Yahoo
    http = Telegram()
    monkeypatch.setattr(tc.requests, "post", http)
    http.prices = {}  # the sweep's quotes; a ticker not listed has no price this poll
    monkeypatch.setattr(lambda_function, "price_of", http.prices.get)

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


def test_pending_ticker_gets_no_new_alert_on_a_later_day(poll, monkeypatch):
    lambda_function.lambda_handler({}, None)
    monkeypatch.setattr(lambda_function, "trading_date", lambda: "2026-11-17")
    lambda_function.lambda_handler({}, None)
    assert len(alert_store().list_alerts()) == 1


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
                        lambda: {**base, "maxCandidatesPerRun": 1, "volumeThreshold": 1.0,
                                 "requestStrategies": ["macd", "volume"]})
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
                        lambda: {**base, "volumeThreshold": 1.0, "tradingEnabled": on, "dollarsPerTrade": dollars,
                                 "requestStrategies": ["macd", "volume"]})


def _buttons(payload):
    return [b["callback_data"] for row in (payload.get("reply_markup") or {}).get("inline_keyboard", []) for b in row]


def test_order_line_and_amount_buttons_only_on_admin_copy(poll, monkeypatch):
    _trading(monkeypatch, True)
    lambda_function.lambda_handler({}, None)
    sends = poll.sends()
    admin = [p for p in sends if str(p["chat_id"]) == ADMIN][0]
    sub = [p for p in sends if str(p["chat_id"]) == SUB][0]
    assert "Suggested: 2.741228 NVDA at about $182.40, about $500.00 (MOCK)." in admin["text"]
    data = _buttons(admin)
    assert [d.split(":")[0] for d in data] == ["bq", "bd", "ba", "reject"]
    assert data[0].endswith(":2.741228") and data[1].endswith(":500")
    assert "Suggested" not in sub["text"] and _buttons(sub) == []


def test_small_dollar_amount_suggests_a_fraction(poll, monkeypatch):
    _trading(monkeypatch, True, dollars=100)
    lambda_function.lambda_handler({}, None)
    admin = [p for p in poll.sends() if str(p["chat_id"]) == ADMIN][0]
    assert "Suggested: 0.548245 NVDA at about $182.40, about $100.00 (MOCK)." in admin["text"]


def test_trading_off_keeps_approve_and_reject(poll, monkeypatch):
    _trading(monkeypatch, False)
    lambda_function.lambda_handler({}, None)
    assert not any("Suggested" in p["text"] or "MOCK" in p["text"] for p in poll.sends())
    admin = [p for p in poll.sends() if str(p["chat_id"]) == ADMIN][0]
    assert [d.split(":")[0] for d in _buttons(admin)] == ["approve", "reject"]


# ---------- strategies: checklist on every alert, MACD alerts ----------

def _macd_buy_bars():
    """The series tests/test_strategies.py uses: a dip with a pullback, then a rebound (a MACD BUY)."""
    from tests.test_indicators import bars_from

    c = [100 + 0.3 * i for i in range(300)]
    for k in range(7):
        c[293 + k] = c[292] - 4 if k < 3 else c[292] - 4 + (k - 2) * 1.5
    return bars_from(c)


MACD_TICKER = "AAPL"  # in UNIVERSE, not in FakeScan's results


def _bars(monkeypatch, bars):
    monkeypatch.setattr(lambda_function, "completed_bars", lambda tickers, today: bars)


def _by_strategy(strategy):
    return [a for a in alert_store().list_alerts() if a["strategy"] == strategy]


def test_volume_alert_stores_checklist_fields(poll, monkeypatch):
    _bars(monkeypatch, {"NVDA": _macd_buy_bars()})
    lambda_function.lambda_handler({}, None)
    alert = _by_strategy("volume")[0]
    assert alert["signalType"] == "VOLUME_BREAKTHROUGH"
    assert [x["key"] for x in alert["checklist"]] == ["uptrend", "macd", "volume", "price_action", "reward_risk"]
    assert alert["confidence"] == sum(x["points"] for x in alert["checklist"] if x["passed"])
    assert alert["confidence"] > 0
    assert alert["entryPrice"] == 182.4


def test_macd_buy_raises_one_macd_alert(poll, monkeypatch):
    bars = _macd_buy_bars()
    _bars(monkeypatch, {MACD_TICKER: bars})
    lambda_function.lambda_handler({}, None)
    macd_alerts = _by_strategy("macd")
    assert len(macd_alerts) == 1
    a = macd_alerts[0]
    assert a["ticker"] == MACD_TICKER and a["signalType"] == "MACD_CROSSOVER"
    assert a["entryPrice"] == round(bars[-1][3], 2) == a["priceAtAlert"]
    assert a["tradingDate"] == "2026-11-16"
    volume_item = next(x for x in a["checklist"] if x["key"] == "volume")
    assert volume_item["detail"] == "No volume reading"  # not in this poll's scan


def _approved_volume_alert(ticker="NVDA"):
    """A decided volume alert today: blocks a second volume alert but not a MACD one."""
    db = alert_store()
    db.save_alert(alert_id="seed", ticker=ticker, signal_details="v", pros_cons="p",
                  strategy="volume", trading_date="2026-11-16")
    db.update_alert_status("seed", "APPROVED")


def test_macd_alert_uses_the_scan_volume_score(poll, monkeypatch):
    _approved_volume_alert()
    _bars(monkeypatch, {"NVDA": _macd_buy_bars()})
    lambda_function.lambda_handler({}, None)
    a = _by_strategy("macd")[0]
    volume_item = next(x for x in a["checklist"] if x["key"] == "volume")
    assert volume_item["passed"] and "9.00x" in volume_item["detail"]


def test_macd_alert_price_is_the_scan_live_price_when_available(poll, monkeypatch):
    _approved_volume_alert()
    bars = _macd_buy_bars()
    _bars(monkeypatch, {"NVDA": bars})
    lambda_function.lambda_handler({}, None)
    a = _by_strategy("macd")[0]
    assert a["priceAtAlert"] == 182.4  # the scan's live price for NVDA
    assert a["entryPrice"] == round(bars[-1][3], 2)  # the checklist entry rule is unchanged


def _dated(bars, *last_dates):
    """The same closes, with the last bars relabelled to the given dates."""
    head = bars[: len(bars) - len(last_dates)]
    return head + [(d, *b[1:]) for d, b in zip(last_dates, bars[len(head):])]


def test_one_crossover_raises_one_macd_alert_over_three_trading_days(poll, monkeypatch):
    bars = _macd_buy_bars()
    days = [("2026-11-16", ("2026-11-12", "2026-11-13")),
            ("2026-11-17", ("2026-11-13", "2026-11-16")),
            ("2026-11-18", ("2026-11-16", "2026-11-17"))]
    for today, last in days:
        monkeypatch.setattr(lambda_function, "trading_date", lambda t=today: t)
        _bars(monkeypatch, {MACD_TICKER: _dated(bars, *last)})
        lambda_function.lambda_handler({}, None)
    assert len(_by_strategy("macd")) == 1


def test_one_alert_read_per_poll(poll, monkeypatch):
    _bars(monkeypatch, {MACD_TICKER: _macd_buy_bars()})
    from utils.storage import SqliteStore

    real, reads = SqliteStore.list_alerts, []
    monkeypatch.setattr(SqliteStore, "list_alerts", lambda self, *a, **k: reads.append(1) or real(self, *a, **k))

    def per_ticker(*a):
        raise AssertionError("per-ticker alert read")

    monkeypatch.setattr(SqliteStore, "alert_exists", per_ticker)
    lambda_function.lambda_handler({}, None)
    assert len(reads) == 1


def test_macd_runs_once_per_day_and_dedupes_after_a_restart(poll, monkeypatch):
    _bars(monkeypatch, {MACD_TICKER: _macd_buy_bars()})
    lambda_function.lambda_handler({}, None)
    from strategies import macd

    real = macd.evaluate
    calls = []
    monkeypatch.setattr(macd, "evaluate", lambda *a: calls.append(a) or real(*a))
    lambda_function.lambda_handler({}, None)
    assert calls == []  # same process, same day: the MACD pass is skipped
    monkeypatch.setattr(lambda_function, "_macd_days", set())  # a fresh process
    lambda_function.lambda_handler({}, None)
    assert calls  # it ran again...
    assert len(_by_strategy("macd")) == 1  # ...and alert_exists kept it to one


def test_ticker_without_history_gets_no_macd_alert_and_a_volume_only_alert(poll):
    lambda_function.lambda_handler({}, None)  # completed_bars gives {}
    assert _by_strategy("macd") == []
    alert = _by_strategy("volume")[0]
    assert alert["confidence"] == 20
    v = next(x for x in alert["checklist"] if x["key"] == "volume")
    assert v["passed"] and "9.00x" in v["detail"]
    assert all(x["detail"] == "Not enough history" for x in alert["checklist"] if x["key"] != "volume")
    assert alert.get("stopPrice") is None and alert.get("targetPrice") is None


def test_macd_alerts_count_toward_the_run_cap(poll, monkeypatch):
    from utils import settings_store

    base = settings_store.defaults()
    monkeypatch.setattr(lambda_function, "effective_settings",
                        lambda: {**base, "maxCandidatesPerRun": 1, "volumeThreshold": 1.0,
                                 "requestStrategies": ["macd", "volume"]})
    _bars(monkeypatch, {MACD_TICKER: _macd_buy_bars()})
    result = lambda_function.lambda_handler({}, None)
    assert result["body"] == "Processed 1 signals."
    assert _by_strategy("macd") == []
    lambda_function.lambda_handler({}, None)  # the cut-short MACD pass is not marked done
    assert len(_by_strategy("macd")) == 1


def test_telegram_copies_carry_the_conditions_line(poll):
    lambda_function.lambda_handler({}, None)
    sends = poll.sends()
    assert sends and all("Conditions met: 1 of 5 (20 points)" in p["text"] for p in sends)


def test_llm_gets_the_checklist_and_strategy_notes(poll, monkeypatch):
    _bars(monkeypatch, {MACD_TICKER: _macd_buy_bars()})
    lambda_function.lambda_handler({}, None)
    by_ticker = {c["ticker"]: c for c in FakeLlm.calls}
    assert len(by_ticker["NVDA"]["checklist"]) == 5 and by_ticker["NVDA"]["notes"] == ""
    assert len(by_ticker[MACD_TICKER]["checklist"]) == 5
    assert by_ticker[MACD_TICKER]["notes"].startswith("## 3.1")


def test_llm_prompt_lists_checklist_and_notes():
    from types import SimpleNamespace

    from utils.llm_client import LlmClient

    sent = {}

    def create(**kw):
        sent.update(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    llm = LlmClient.__new__(LlmClient)
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    items = [{"key": "uptrend", "label": "Uptrend", "passed": True, "points": 25, "detail": "close 110 > SMA 100"},
             {"key": "macd", "label": "MACD", "passed": False, "points": 25, "detail": "no cross"}]
    assert llm.analyze_stock("AAPL", "sig", {}, checklist=items, strategy_notes="# Rules") == "ok"
    prompt = sent["messages"][-1]["content"]
    assert "PASS Uptrend: close 110 > SMA 100" in prompt
    assert "FAIL MACD: no cross" in prompt
    assert "authoritative" in prompt
    assert "Strategy notes:\n# Rules" in prompt
    llm.analyze_stock("AAPL", "sig", {})
    assert "Strategy notes" not in sent["messages"][-1]["content"]


# ---------- expiry sweep and new-alert gates ----------

from datetime import datetime, timedelta, timezone  # noqa: E402

from utils.market_clock import trading_date as real_trading_date  # noqa: E402

EXPIRED_TEXT = "NVDA request expired: Price ran more than 3% above the alert price $182.40."


def _ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _pending(messages=None, legacy_message_id=None, strategy="volume", **extra):
    db = alert_store()
    db.save_alert(alert_id="a1", ticker="NVDA", signal_details="v", pros_cons="p", price=182.4,
                  strategy=strategy, trading_date="2026-11-13", chat_id=ADMIN,
                  message_id=legacy_message_id, messages=messages, **extra)


def _methods(poll, method):
    return [p for m, p in poll.calls if m == method]


def test_poll_stores_every_recipient_message(poll):
    lambda_function.lambda_handler({}, None)
    alert = alert_store().list_alerts()[0]
    assert sorted(m["chatId"] for m in alert["telegramMessages"]) == [ADMIN, SUB]
    assert all(m["messageId"] and m["sentAt"] for m in alert["telegramMessages"])


def test_off_hours_poll_expires_an_invalid_request_and_raises_nothing(poll, monkeypatch):
    lambda_function.lambda_handler({}, None)  # NVDA alert at $182.40, copies to admin and subscriber
    monkeypatch.setattr(lambda_function, "session_fraction", lambda: None)

    def boom(self, *a, **k):
        raise AssertionError("scan ran while the market was closed")

    monkeypatch.setattr(FakeScan, "get_market_scan_results", boom)
    poll.prices["NVDA"] = 190.0  # more than 3% above
    poll.calls.clear()
    result = lambda_function.lambda_handler({}, None)

    assert result["body"] == "Market closed; skipping scan."
    alert = alert_store().list_alerts()[0]
    assert alert["status"] == "EXPIRED"
    assert sorted(str(p["chat_id"]) for p in _methods(poll, "deleteMessage")) == [ADMIN, SUB]
    notes = poll.sends()
    assert [(str(p["chat_id"]), p["text"]) for p in notes] == [(ADMIN, EXPIRED_TEXT)]  # admins only


def test_old_copies_are_edited_and_recent_ones_deleted(poll, monkeypatch):
    monkeypatch.setattr(lambda_function, "session_fraction", lambda: None)
    _pending(messages=[{"chatId": ADMIN, "messageId": 7, "sentAt": _ago(50)},
                       {"chatId": SUB, "messageId": 8, "sentAt": _ago(2)}])
    poll.prices["NVDA"] = 190.0
    lambda_function.lambda_handler({}, None)
    edits = _methods(poll, "editMessageText")
    assert edits == [{"chat_id": ADMIN, "message_id": 7, "text": EXPIRED_TEXT}]
    assert "reply_markup" not in edits[0]
    assert _methods(poll, "deleteMessage") == [{"chat_id": SUB, "message_id": 8}]


def test_alert_without_stored_copies_falls_back_to_the_admin_copy(poll, monkeypatch):
    monkeypatch.setattr(lambda_function, "session_fraction", lambda: None)
    _pending(legacy_message_id="99")
    poll.prices["NVDA"] = 190.0
    lambda_function.lambda_handler({}, None)
    assert _methods(poll, "deleteMessage") == [{"chat_id": ADMIN, "message_id": 99}]


def test_a_valid_or_unpriced_request_is_left_alone(poll, monkeypatch):
    monkeypatch.setattr(lambda_function, "session_fraction", lambda: None)
    _pending(messages=[{"chatId": ADMIN, "messageId": 7, "sentAt": _ago(1)}])
    lambda_function.lambda_handler({}, None)  # no price for NVDA
    poll.prices["NVDA"] = 183.0  # within 3%
    lambda_function.lambda_handler({}, None)
    assert alert_store().get_alert("a1")["status"] == "PENDING"
    assert poll.calls == []


def test_request_expired_today_holds_the_ticker_until_the_next_day(poll, monkeypatch):
    today = real_trading_date()  # expiredAt is stamped by the real clock
    monkeypatch.setattr(lambda_function, "trading_date", lambda: today)
    _pending(strategy="macd")  # a MACD request, so `seen` cannot block the volume alert
    poll.prices["NVDA"] = 190.0
    lambda_function.lambda_handler({}, None)  # the sweep expires it, then the scan sees NVDA
    db = alert_store()
    assert [a["status"] for a in db.list_alerts()] == ["EXPIRED"]
    tomorrow = (datetime.fromisoformat(today) + timedelta(days=1)).date().isoformat()
    monkeypatch.setattr(lambda_function, "trading_date", lambda: tomorrow)
    lambda_function.lambda_handler({}, None)
    assert sorted(a["status"] for a in db.list_alerts()) == ["EXPIRED", "PENDING"]


def _alerts_enabled(monkeypatch, on):
    from utils import settings_store

    base = settings_store.defaults()
    monkeypatch.setattr(lambda_function, "effective_settings", lambda: {**base, "telegramAlertsEnabled": on})


def test_no_admin_note_when_alerts_are_off_and_no_copy_was_cleaned(poll, monkeypatch):
    _alerts_enabled(monkeypatch, False)
    monkeypatch.setattr(lambda_function, "session_fraction", lambda: None)
    _pending()
    poll.prices["NVDA"] = 190.0
    lambda_function.lambda_handler({}, None)
    assert alert_store().get_alert("a1")["status"] == "EXPIRED"
    assert poll.sends() == []


def test_admin_note_when_alerts_are_off_but_a_copy_was_cleaned(poll, monkeypatch):
    _alerts_enabled(monkeypatch, False)
    monkeypatch.setattr(lambda_function, "session_fraction", lambda: None)
    _pending(messages=[{"chatId": SUB, "messageId": 8, "sentAt": _ago(2)}])
    poll.prices["NVDA"] = 190.0
    lambda_function.lambda_handler({}, None)
    assert [(str(p["chat_id"]), p["text"]) for p in poll.sends()] == [(ADMIN, EXPIRED_TEXT)]


def test_admin_note_when_alerts_are_on_without_copies(poll, monkeypatch):
    monkeypatch.setattr(lambda_function, "session_fraction", lambda: None)
    _pending()
    poll.prices["NVDA"] = 190.0
    lambda_function.lambda_handler({}, None)
    assert [(str(p["chat_id"]), p["text"]) for p in poll.sends()] == [(ADMIN, EXPIRED_TEXT)]


def test_ticker_rejected_today_is_skipped_and_allowed_next_day(poll, monkeypatch):
    today = real_trading_date()  # reviewedAt is stamped by the real clock
    monkeypatch.setattr(lambda_function, "trading_date", lambda: today)
    lambda_function.lambda_handler({}, None)
    db = alert_store()
    db.update_alert_status(db.list_alerts()[0]["alertId"], "REJECTED")
    lambda_function.lambda_handler({}, None)
    assert len(db.list_alerts()) == 1
    tomorrow = (datetime.fromisoformat(today) + timedelta(days=1)).date().isoformat()
    monkeypatch.setattr(lambda_function, "trading_date", lambda: tomorrow)
    lambda_function.lambda_handler({}, None)
    assert len(db.list_alerts()) == 2


# ---------- bar store (R1) ----------

def test_poll_refreshes_bars_once_per_trading_day(poll, monkeypatch):
    calls = []
    monkeypatch.setattr(lambda_function, "refresh_daily",
                        lambda store, today, tickers: calls.append((str(today), sorted(tickers))) or {"failed": False})
    lambda_function.lambda_handler({}, None)
    lambda_function.lambda_handler({}, None)
    assert len(calls) == 1 and calls[0][0] == "2026-11-16"
    assert "SPY" in calls[0][1] and "XLK" in calls[0][1] and "NVDA" in calls[0][1]


def test_failed_refresh_is_retried_after_thirty_minutes(poll, monkeypatch):
    calls = []
    monkeypatch.setattr(lambda_function, "refresh_daily", lambda *a: calls.append(1) or {"failed": True})
    lambda_function.lambda_handler({}, None)
    lambda_function.lambda_handler({}, None)  # next poll: not yet
    assert len(calls) == 1
    monkeypatch.setattr(lambda_function, "_bar_retry_at", 0.0)  # 30 minutes later
    lambda_function.lambda_handler({}, None)
    assert len(calls) == 2


def test_poll_skips_a_ticker_with_invalid_bars(poll):
    from market_data.bar_store import BarStore

    BarStore(Config.SQLITE_PATH).save_report("NVDA", {"status": "invalid", "issues": ["Stale: newest bar 2026-11-10"]})
    lambda_function.lambda_handler({}, None)
    assert all(a["ticker"] != "NVDA" for a in alert_store().list_alerts())


def test_saturday_poll_runs_both_backtests_once(poll, monkeypatch):
    runs = []
    monkeypatch.setattr(lambda_function, "trading_date", lambda: "2026-11-21")  # a Saturday
    monkeypatch.setattr(lambda_function, "run_and_store", lambda bars, store, strategy, tickers, today: runs.append(strategy))
    lambda_function.lambda_handler({}, None)
    lambda_function.lambda_handler({}, None)
    assert sorted(runs) == ["macd", "volume"]


def test_weekday_poll_runs_no_backtest(poll, monkeypatch):
    runs = []
    monkeypatch.setattr(lambda_function, "run_and_store", lambda *a: runs.append(a))
    lambda_function.lambda_handler({}, None)  # Monday 2026-11-16
    assert runs == []


def test_outcomes_are_stored_after_the_bar_refresh(poll):
    from backtesting.store import ResearchStore
    from market_data.bar_store import BarStore
    from market_data.bars import Bar

    alert_store().save_alert("o1", "MSFT", "MACD", "x", trading_date="2026-11-12", price=100.0, strategy="macd",
                             stop=95.0, target=110.0)
    BarStore(Config.SQLITE_PATH).upsert("MSFT", [Bar("2026-11-13", 112, 99, 111, 100, 1, 111)])
    lambda_function.lambda_handler({}, None)
    [row] = ResearchStore(Config.SQLITE_PATH).outcomes("macd")
    assert row["alertId"] == "o1" and row["reason"] == "target"


def test_stored_bars_gone_stale_block_alerts_even_with_an_old_valid_report(poll):
    from market_data.bar_store import BarStore
    from market_data.bars import Bar

    store = BarStore(Config.SQLITE_PATH)
    store.upsert("NVDA", [Bar("2026-11-06", 11, 9, 10, 10, 1, 10)])  # Yahoo down since: newest bar a week old
    store.save_report("NVDA", {"status": "valid", "issues": []})
    lambda_function.lambda_handler({}, None)
    assert all(a["ticker"] != "NVDA" for a in alert_store().list_alerts())


def test_partial_refresh_is_retried_later_not_marked_done(poll, monkeypatch):
    calls = []
    monkeypatch.setattr(lambda_function, "refresh_daily",
                        lambda *a: calls.append(1) or {"failed": False, "missing": ["MSFT"]})
    monkeypatch.setattr(lambda_function, "_bar_retry_at", 0.0)
    lambda_function.lambda_handler({}, None)
    lambda_function.lambda_handler({}, None)  # within 30 minutes: no second download
    assert len(calls) == 1 and "2026-11-16" not in lambda_function._bar_days
    monkeypatch.setattr(lambda_function, "_bar_retry_at", 0.0)  # 30 minutes later
    lambda_function.lambda_handler({}, None)
    assert len(calls) == 2


def test_a_refresh_error_does_not_stop_the_scan(poll, monkeypatch):
    def boom(*a):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(lambda_function, "refresh_daily", boom)
    monkeypatch.setattr(lambda_function, "stop_watch", boom)
    lambda_function.lambda_handler({}, None)
    assert any(a["ticker"] == "NVDA" for a in alert_store().list_alerts())


def test_half_day_waits_thirty_minutes(poll, monkeypatch):
    monkeypatch.setattr(lambda_function, "trading_date", lambda: "2026-11-27")  # 13:00 close
    monkeypatch.setattr(lambda_function, "session_fraction", lambda: 20 / 210)  # 20 minutes in
    assert lambda_function.lambda_handler({}, None)["body"] == "Too early in the session; skipping scan."


# ---------- volume is dashboard only by default (owner, 2026-11-18) ----------

def test_by_default_a_volume_signal_is_shown_but_not_requested(poll):
    from utils.settings_store import SettingsStore

    SettingsStore(Config.SQLITE_PATH).put({"requestStrategies": ["macd"]})
    lambda_function.lambda_handler({}, None)
    [alert] = [a for a in alert_store().list_alerts() if a["ticker"] == "NVDA"]
    assert alert["status"] == "INFO" and alert["strategy"] == "volume"
    assert alert.get("aiProsCons")  # still analysed for the dashboard
    assert poll.sends() == []  # nothing to Telegram


def test_info_alerts_do_not_block_requests_or_use_the_cap(poll, monkeypatch):
    from utils.settings_store import SettingsStore, defaults

    base = defaults()
    monkeypatch.setattr(lambda_function, "effective_settings",
                        lambda: {**base, "volumeThreshold": 1.0, "maxCandidatesPerRun": 1, "requestStrategies": ["macd"]})
    monkeypatch.setattr(FakeScan, "get_market_scan_results", lambda self, **k: [
        {"ticker": "NVDA", "volume_score": 9.0, "price": 182.4},
        {"ticker": "AMD", "volume_score": 5.0, "price": 120.0},
    ])
    lambda_function.lambda_handler({}, None)
    statuses = {a["ticker"]: a["status"] for a in alert_store().list_alerts()}
    assert statuses == {"NVDA": "INFO"}  # the volume loop keeps its own cap of 1
    held = [a for a in alert_store().list_alerts() if a["status"] == "PENDING"]
    assert held == []  # an INFO alert never counts as an open request


# ---------- P6: earnings and health ----------

def test_request_message_carries_the_earnings_warning(poll):
    from datetime import date
    from market_data.earnings import EarningsStore

    EarningsStore(Config.SQLITE_PATH).save({"NVDA": date(2026, 11, 17)})  # poll date 2026-11-16: next trading day
    lambda_function.lambda_handler({}, None)
    admin = [p for p in poll.sends() if str(p["chat_id"]) == ADMIN][0]["text"]
    assert "Earnings in 1 day (Nov 17)" in admin


def test_bar_refresh_also_refreshes_earnings_and_survives_a_failure(poll, monkeypatch):
    from datetime import date
    from market_data.earnings import EarningsStore

    monkeypatch.setattr(lambda_function, "download_earnings", lambda tickers, today: {"WMT": date(2026, 11, 19)})
    lambda_function.lambda_handler({}, None)
    assert EarningsStore(Config.SQLITE_PATH).get(["WMT"]) == {"WMT": date(2026, 11, 19)}

    def boom(*a):
        raise RuntimeError("Yahoo down")

    monkeypatch.setattr(lambda_function, "download_earnings", boom)
    monkeypatch.setattr(lambda_function, "_bar_days", set())
    lambda_function.lambda_handler({}, None)  # no crash; the stored date stays
    assert EarningsStore(Config.SQLITE_PATH).get(["WMT"]) == {"WMT": date(2026, 11, 19)}


def test_poll_records_its_run_and_the_ai_state(poll, monkeypatch):
    from utils.health import HealthStore
    from utils.llm_client import FAILED_TEXT

    lambda_function.lambda_handler({}, None)
    h = HealthStore(Config.SQLITE_PATH).get_all()
    assert h["lastPollAt"] and h["lastAiOkAt"] and h.get("aiFailingSince") is None

    monkeypatch.setattr(FakeLlm, "analyze_stock", lambda self, *a, **k: FAILED_TEXT)
    monkeypatch.setattr(FakeScan, "get_market_scan_results", lambda self, **k: [
        {"ticker": "AMD", "volume_score": 5.0, "price": 120.0}])
    lambda_function.lambda_handler({}, None)
    first = HealthStore(Config.SQLITE_PATH).get_all()["aiFailingSince"]
    monkeypatch.setattr(FakeScan, "get_market_scan_results", lambda self, **k: [
        {"ticker": "AAPL", "volume_score": 5.0, "price": 180.0}])
    lambda_function.lambda_handler({}, None)
    assert first and HealthStore(Config.SQLITE_PATH).get_all()["aiFailingSince"] == first  # kept from the first failure
