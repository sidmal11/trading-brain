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


def test_macd_alert_uses_the_scan_volume_score(poll, monkeypatch):
    _bars(monkeypatch, {"NVDA": _macd_buy_bars()})
    lambda_function.lambda_handler({}, None)
    a = _by_strategy("macd")[0]
    volume_item = next(x for x in a["checklist"] if x["key"] == "volume")
    assert volume_item["passed"] and "9.00x" in volume_item["detail"]


def test_macd_alert_price_is_the_scan_live_price_when_available(poll, monkeypatch):
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
                        lambda: {**base, "maxCandidatesPerRun": 1, "volumeThreshold": 1.0})
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
