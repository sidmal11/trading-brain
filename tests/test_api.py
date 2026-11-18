"""Contract tests for the read API.

These assert the shapes `frontend/src/types/market.ts` declares, because the
dashboard parses the response directly: a renamed field here renders a blank
card there with no error anywhere in between.

DynamoDB and Yahoo Finance are both replaced with fakes. The point is the
contract and the decision logic, not that boto3 and yfinance work.
"""

import sys
from pathlib import Path

import copy

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import main as api_main  # noqa: E402
from api import market_source  # noqa: E402


class FakeDb:
    def __init__(self, alerts=None):
        self.alerts = alerts if alerts is not None else []
        self.updates = []
        self.update_succeeds = True

    def list_alerts(self, limit=200):
        return sorted(self.alerts, key=lambda a: a.get("timestamp") or "", reverse=True)[:limit]

    def get_alert(self, alert_id):
        return next((a for a in self.alerts if a["alertId"] == alert_id), None)

    def set_prompt(self, chat_id, alert_id):
        pass

    def get_prompt(self, chat_id):
        return None

    def clear_prompt(self, chat_id):
        pass

    def update_alert_status(self, alert_id, status):
        if not self.update_succeeds:
            return False
        self.updates.append((alert_id, status))
        for alert in self.alerts:
            if alert["alertId"] == alert_id:
                alert["status"] = status
        return True


ALERT = {
    "alertId": "alert-1",
    "ticker": "NVDA",
    "timestamp": "2026-11-15T14:30:00+00:00",
    "signalDetails": "Volume Breakthrough detected with score 3.4",
    "aiProsCons": "Pros\n- Strong demand\n- Margin expansion\nCons\n- Rich valuation",
    "status": "PENDING",
    "telegramMessageId": "9850",
}

QUOTE = {
    "symbol": "NVDA",
    "companyName": "NVIDIA Corporation",
    "sector": "Technology",
    "industry": "Semiconductors",
    "marketCap": 3_000_000_000_000,
    "price": 128.45,
    "change": 5.6,
    "changePercent": 4.58,
    "volume": 400,
    "averageVolume": 200,
    "volumeSurgeRatio": 2.0,
    "history": [
        {"timestamp": "2026-11-15T14:30:00+00:00", "timeLabel": "14:30", "open": 1.0,
         "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10},
        {"timestamp": "2026-11-15T14:35:00+00:00", "timeLabel": "14:35", "open": 1.5,
         "high": 2.5, "low": 1.0, "close": 2.0, "volume": 99},
    ],
}


@pytest.fixture
def db():
    return FakeDb([dict(ALERT)])


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(market_source, "quote", lambda symbol: copy.deepcopy(QUOTE))
    monkeypatch.setattr(market_source, "index_quote", lambda symbol: {"price": 100.0, "changePercent": 1.0})
    monkeypatch.setattr(api_main, "TelegramClient", lambda *a, **k: _NoopTelegram())
    monkeypatch.setattr(api_main.Config, "ALLOW_UNAUTHENTICATED", True)

    app = api_main.create_app()
    app.dependency_overrides[api_main.get_db] = lambda: db
    app.dependency_overrides[api_main.get_subscribers] = lambda: FakeSubscribers()
    return TestClient(app)


class FakeSubscribers:
    def __init__(self):
        self.subscribed = []
        self.deactivated = []

    def subscribe(self, chat_id, username=None, first_name=None):
        self.subscribed.append(chat_id)
        return True

    def deactivate(self, chat_id):
        self.deactivated.append(chat_id)
        return True


class _NoopTelegram:
    admin_chat_ids = {"42"}

    def __init__(self):
        self.calls = []
        self.texts = []

    def update_message_decision(self, *args, **kwargs):
        self.calls.append((args, kwargs))

    def answer_callback_query(self, *args):
        self.calls.append(args)

    def send_text(self, chat_id, text, keyboard=None):
        self.texts.append((chat_id, text))

    def clear_buttons(self, chat_id, message_id):
        pass


# ── Health ────────────────────────────────────────────────────────────────────

def test_health_is_open_and_reports_auth_state(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    # No pool configured in tests, so the API must admit it is not enforcing.
    assert response.json()["authEnforced"] is False


# ── Signals ───────────────────────────────────────────────────────────────────

def test_signals_match_the_stocksignal_contract(client):
    body = client.get("/api/v1/signals").json()
    assert len(body) == 1
    signal = body[0]

    for field in (
        "id", "ticker", "companyName", "sector", "industry", "marketCap",
        "marketCapCategory", "currentPrice", "priceChange", "priceChangePercent",
        "volume", "averageVolume", "volumeSurgeRatio",
        "signalType", "signalDescription", "alertStatus", "detectedAt",
        "aiAnalysis", "chartData",
    ):
        assert field in signal, f"missing {field}"

    assert signal["ticker"] == "NVDA"
    assert signal["marketCapCategory"] == "MEGA"
    assert signal["alertStatus"] == "PENDING"


CHECKLIST = [{"key": k, "label": k, "passed": True, "points": 20, "detail": "d"}
             for k in ("uptrend", "macd", "volume", "price_action", "reward_risk")]


def test_checklist_alert_reports_stored_confidence_and_conditions(db, client):
    from decimal import Decimal
    db.alerts[0].update(signalType="MACD_CROSSOVER", strategy="macd", confidence=Decimal("85"),
                        checklist=[{**c, "points": Decimal("20")} for c in CHECKLIST],
                        entryPrice=Decimal("100.5"), stopPrice=Decimal("95"), targetPrice=Decimal("108.75"))
    signal = client.get("/api/v1/signals").json()[0]
    assert signal["signalType"] == "MACD_CROSSOVER" and signal["strategy"] == "macd"
    assert signal["aiAnalysis"]["confidenceScore"] == 85
    assert [c["key"] for c in signal["conditions"]] == ["uptrend", "macd", "volume", "price_action", "reward_risk"]
    assert signal["conditions"][0]["points"] == 20
    assert (signal["entryPrice"], signal["stopPrice"], signal["targetPrice"]) == (100.5, 95.0, 108.75)
    assert signal["suggestedShares"] == 1.494768  # $50 risk / ($128.45 - $95)


def test_legacy_alert_keeps_pros_cons_score_and_empty_conditions(client):
    signal = client.get("/api/v1/signals").json()[0]
    assert signal["conditions"] == [] and signal["strategy"] is None
    assert signal["suggestedShares"] == 3.892565  # no stop: $500 / $128.45
    assert (signal["entryPrice"], signal["stopPrice"], signal["targetPrice"]) == (None, None, None)
    assert signal["aiAnalysis"]["confidenceScore"] == market_source._confidence(
        ["Strong demand", "Margin expansion"], ["Rich valuation"])


def test_signal_parses_pros_and_cons_out_of_the_llm_prose(client):
    analysis = client.get("/api/v1/signals").json()[0]["aiAnalysis"]
    assert analysis["bullishCatalysts"] == ["Strong demand", "Margin expansion"]
    assert analysis["bearishRisks"] == ["Rich valuation"]
    assert analysis["sentiment"] == "BULLISH"
    assert 0 <= analysis["confidenceScore"] <= 100


def test_signal_tags_the_highest_volume_bar(client):
    chart = client.get("/api/v1/signals").json()[0]["chartData"]
    tagged = [point for point in chart if point.get("signalTag")]
    assert len(tagged) == 1
    assert tagged[0]["volume"] == 99


def test_macd_signal_gets_no_volume_breakthrough_tag(client, db):
    db.alerts[0].update(signalType="MACD_CROSSOVER")
    chart = client.get("/api/v1/signals").json()[0]["chartData"]
    assert not any(point.get("signalTag") for point in chart)


def test_missing_market_cap_is_null_not_small(client, monkeypatch):
    monkeypatch.setattr(market_source, "quote", lambda symbol: {**QUOTE, "marketCap": None})
    signal = client.get("/api/v1/signals").json()[0]
    assert signal["marketCap"] is None and signal["marketCapCategory"] is None


def test_signal_is_dropped_when_its_quote_cannot_be_fetched(client, monkeypatch):
    monkeypatch.setattr(market_source, "quote", lambda symbol: None)
    # A card with a zeroed price would look like real data, so the row is
    # omitted rather than served half-empty.
    assert client.get("/api/v1/signals").json() == []


def test_one_failing_row_does_not_empty_the_response(client, db, monkeypatch):
    db.alerts.append({**ALERT, "alertId": "alert-2", "ticker": "BOOM"})

    def flaky(symbol):
        if symbol == "BOOM":
            raise RuntimeError("upstream exploded")
        return dict(QUOTE)

    monkeypatch.setattr(market_source, "quote", flaky)
    body = client.get("/api/v1/signals").json()
    assert [signal["ticker"] for signal in body] == ["NVDA"]


# ── Alerts ────────────────────────────────────────────────────────────────────

def test_alerts_are_returned_unshaped_for_the_client_contract(client):
    body = client.get("/api/v1/alerts").json()
    assert body[0]["alertId"] == "alert-1"
    assert body[0]["aiProsCons"].startswith("Pros")


def test_review_records_the_decision(client, db):
    response = client.post("/api/v1/alerts/alert-1/review", json={"decision": "APPROVED"})
    assert response.status_code == 200
    assert response.json() == {"success": True, "alertId": "alert-1", "status": "APPROVED", "order": None}
    assert db.updates == [("alert-1", "APPROVED")]


def test_review_accepts_lowercase_decisions(client, db):
    assert client.post("/api/v1/alerts/alert-1/review", json={"decision": "approved"}).status_code == 200
    assert db.updates == [("alert-1", "APPROVED")]


def test_review_rejects_an_unknown_decision(client, db):
    response = client.post("/api/v1/alerts/alert-1/review", json={"decision": "MAYBE"})
    assert response.status_code == 422
    assert db.updates == []


def test_review_of_a_missing_alert_is_404(client):
    assert client.post("/api/v1/alerts/nope/review", json={"decision": "APPROVED"}).status_code == 404


def test_review_surfaces_a_write_failure(client, db):
    db.update_succeeds = False
    assert client.post("/api/v1/alerts/alert-1/review", json={"decision": "APPROVED"}).status_code == 502


# ── Market overview ───────────────────────────────────────────────────────────

def test_market_overview_counts_the_pending_queue(client, db):
    db.alerts.append({**ALERT, "alertId": "alert-2", "status": "APPROVED"})
    body = client.get("/api/v1/market-overview").json()

    assert body["pendingApprovalsCount"] == 1
    assert set(body["sp500"]) == {"price", "changePercent"}
    assert body["status"] in {"PRE_MARKET", "REGULAR_HOURS", "POST_MARKET", "CLOSED"}


def test_unread_index_is_null_not_zero(client, monkeypatch):
    monkeypatch.setattr(market_source, "index_quote", lambda symbol: None)
    body = client.get("/api/v1/market-overview").json()
    for key in ("sp500", "nasdaq", "vix"):
        assert body[key] == {"price": None, "changePercent": None}


# ── Telegram webhook ──────────────────────────────────────────────────────────

def _callback(data, tapper=42):
    """A button tap. `tapper` is the chat that pressed the button (42 is the admin in tests)."""
    return {"callback_query": {
        "id": "cb-1",
        "data": data,
        "from": {"id": tapper},
        "message": {"message_id": 7, "chat": {"id": 42}},
    }}


def _message(text, chat_type="private", chat_id=99):
    return {"message": {"text": text, "chat": {"id": chat_id, "type": chat_type, "username": "sid"}}}


def test_webhook_is_refused_when_no_secret_is_configured(client, monkeypatch):
    monkeypatch.setattr(api_main.Config, "TELEGRAM_WEBHOOK_SECRET", "")
    response = client.post("/api/v1/telegram/webhook", json=_callback("approve:alert-1:NVDA"))
    # Accepting unauthenticated approvals would let anyone who finds the URL
    # approve trades, so an unconfigured webhook refuses outright.
    assert response.status_code == 503


def test_webhook_rejects_a_bad_secret(client, monkeypatch):
    monkeypatch.setattr(api_main.Config, "TELEGRAM_WEBHOOK_SECRET", "s3cret")
    response = client.post(
        "/api/v1/telegram/webhook",
        json=_callback("approve:alert-1:NVDA"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert response.status_code == 401


def test_webhook_records_an_approval(client, db, monkeypatch):
    monkeypatch.setattr(api_main.Config, "TELEGRAM_WEBHOOK_SECRET", "s3cret")
    response = client.post(
        "/api/v1/telegram/webhook",
        json=_callback("approve:alert-1:NVDA"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert response.status_code == 200
    assert db.updates == [("alert-1", "APPROVED")]


def test_webhook_ignores_unparseable_callback_data(client, db, monkeypatch):
    monkeypatch.setattr(api_main.Config, "TELEGRAM_WEBHOOK_SECRET", "s3cret")
    response = client.post(
        "/api/v1/telegram/webhook",
        json=_callback("drop-table:alert-1:NVDA"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    # 200 so Telegram stops retrying, but nothing is written.
    assert response.status_code == 200
    assert db.updates == []


def test_webhook_refuses_a_decision_from_a_subscriber(client, db, monkeypatch):
    monkeypatch.setattr(api_main.Config, "TELEGRAM_WEBHOOK_SECRET", "s3cret")
    response = client.post(
        "/api/v1/telegram/webhook",
        json=_callback("approve:alert-1:NVDA", tapper=555),
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    # Only the operator's chat can decide; a subscriber's tap must not reach the DB.
    assert response.status_code == 200
    assert db.updates == []


def test_start_subscribes_the_chat_and_replies(client, monkeypatch):
    monkeypatch.setattr(api_main.Config, "TELEGRAM_WEBHOOK_SECRET", "s3cret")
    subs = FakeSubscribers()
    client.app.dependency_overrides[api_main.get_subscribers] = lambda: subs
    response = client.post(
        "/api/v1/telegram/webhook", json=_message("/start"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert response.status_code == 200
    assert subs.subscribed == ["99"]


def test_stop_unsubscribes_the_chat(client, monkeypatch):
    monkeypatch.setattr(api_main.Config, "TELEGRAM_WEBHOOK_SECRET", "s3cret")
    subs = FakeSubscribers()
    client.app.dependency_overrides[api_main.get_subscribers] = lambda: subs
    response = client.post(
        "/api/v1/telegram/webhook", json=_message("/stop"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert response.status_code == 200
    assert subs.deactivated == ["99"]


def test_commands_in_a_group_chat_are_ignored(client, monkeypatch):
    monkeypatch.setattr(api_main.Config, "TELEGRAM_WEBHOOK_SECRET", "s3cret")
    subs = FakeSubscribers()
    client.app.dependency_overrides[api_main.get_subscribers] = lambda: subs
    response = client.post(
        "/api/v1/telegram/webhook", json=_message("/start", chat_type="group", chat_id=-100),
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert response.status_code == 200
    assert subs.subscribed == []


def test_app_refuses_to_start_without_any_login(monkeypatch):
    monkeypatch.setattr(api_main.Config, "AUTH_MODE", "")
    monkeypatch.setattr(api_main.Config, "ALLOW_UNAUTHENTICATED", False)
    monkeypatch.setattr(api_main, "auth_required", lambda: False)
    with pytest.raises(RuntimeError, match="No login is configured"):
        api_main.create_app()


def test_a_successful_quote_is_reused_within_the_cache_window(monkeypatch):
    calls = []
    monkeypatch.setattr(market_source, "_fetch_quote", lambda symbol: calls.append(symbol) or {"price": 1.0})
    monkeypatch.setattr(market_source, "_cache", {})

    market_source.quote("ZZZ")
    market_source.quote("ZZZ")

    assert calls == ["ZZZ"]


def test_old_api_paths_are_gone(client):
    assert client.get("/api/signals").status_code == 404
    assert client.get("/api/v1/signals").status_code == 200


def test_data_quality_route_lists_reports(client, monkeypatch, tmp_path):
    from market_data.bar_store import BarStore

    monkeypatch.setattr(api_main.Config, "STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(api_main.Config, "SQLITE_PATH", str(tmp_path / "q.db"))
    BarStore(str(tmp_path / "q.db")).save_report("AAPL", {"status": "warn", "issues": ["Volume 0 on 2026-11-16"]})
    [row] = client.get("/api/v1/data-quality").json()
    assert row["ticker"] == "AAPL" and row["status"] == "warn" and row["issues"] == ["Volume 0 on 2026-11-16"]


@pytest.fixture
def research(client, monkeypatch, tmp_path):
    from backtesting.store import ResearchStore

    monkeypatch.setattr(api_main.Config, "STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(api_main.Config, "SQLITE_PATH", str(tmp_path / "r.db"))
    return ResearchStore(str(tmp_path / "r.db"))


def test_strategies_list_without_runs(client, research):
    rows = client.get("/api/v1/strategies").json()
    assert [r["id"] for r in rows] == ["macd", "volume"]
    assert all(r["backtest"] is None and r["live"]["trades"] == 0 and r["live"]["winRate"] is None for r in rows)
    assert rows[0]["name"] == "MACD trend" and rows[0]["version"] == "1"


def test_strategy_performance_has_backtest_trades_and_live_outcomes(client, research):
    window = {"trades": 2, "winRate": 0.5}
    trade = {"ticker": "AAPL", "signal_date": "2026-01-05", "entry_date": "2026-01-06", "entry": 100.0,
             "exit_date": "2026-01-08", "exit": 110.0, "reason": "target", "ret": 0.1, "mfe": 0.1, "mae": -0.01,
             "bars_held": 3}
    research.save_run("macd", "1", "1", {"slippageBps": 5.0}, {"inSample": window, "holdout": window}, [trade])
    research.save_outcomes([
        {"alertId": "a1", "ticker": "AAPL", "strategy": "macd", "signalDate": "2026-11-10", "ret": 0.05, "barsHeld": 4,
         "reason": "target"},
        {"alertId": "a2", "ticker": "MSFT", "strategy": "macd", "signalDate": "2026-11-12", "ret": None, "barsHeld": None,
         "reason": "open"},
    ])
    body = client.get("/api/v1/strategies/macd/performance").json()
    assert body["strategy"]["backtest"]["result"]["holdout"]["winRate"] == 0.5
    assert body["strategy"]["live"]["trades"] == 1 and body["strategy"]["live"]["open"] == 1
    assert body["trades"][0]["ticker"] == "AAPL" and [o["alertId"] for o in body["outcomes"]] == ["a2", "a1"]


def test_unknown_strategy_is_404(client, research):
    assert client.get("/api/v1/strategies/nope/performance").status_code == 404



# ── P6: earnings on signals, health status, pause and resume ──────────────────

@pytest.fixture
def sqlite_api(client, monkeypatch, tmp_path):
    monkeypatch.setattr(api_main.Config, "STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(api_main.Config, "SQLITE_PATH", str(tmp_path / "h.db"))
    monkeypatch.setattr(api_main, "trading_date", lambda: "2026-11-18")
    return str(tmp_path / "h.db")


def test_signals_carry_earnings_date_and_note(client, sqlite_api):
    from datetime import date
    from market_data.earnings import EarningsStore

    EarningsStore(sqlite_api).save({"NVDA": date(2026, 11, 19)})
    signal = client.get("/api/v1/signals").json()[0]
    assert signal["earningsDate"] == "2026-11-19" and signal["earningsNote"] == "Earnings in 1 day (Nov 19)"


def test_signals_without_a_known_date_have_null_earnings(client, sqlite_api):
    signal = client.get("/api/v1/signals").json()[0]
    assert signal["earningsDate"] is None and signal["earningsNote"] is None


def test_health_status(client, sqlite_api, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from market_data.bar_store import BarStore
    from utils.health import HealthStore

    now = datetime.now(timezone.utc)
    HealthStore(sqlite_api).set("lastPollAt", (now - timedelta(minutes=20)).isoformat())
    HealthStore(sqlite_api).set("aiFailingSince", now.isoformat())
    BarStore(sqlite_api).save_report("NVDA", {"status": "invalid", "issues": ["Stale"]})
    BarStore(sqlite_api).save_report("AAPL", {"status": "valid", "issues": []})
    body = client.get("/api/v1/health-status").json()
    assert body["pollStale"] is True and body["aiFailingSince"] and body["skipped"] == ["NVDA"]
    assert body["dataQuality"] == {"valid": 1, "warn": 0, "invalid": 1}

    from utils.settings_store import SettingsStore
    SettingsStore(sqlite_api).put({"pollIntervalMinutes": 30})  # 20 minutes is normal at a 30-minute interval
    assert client.get("/api/v1/health-status").json()["pollStale"] is False


def test_health_status_before_any_poll(client, sqlite_api):
    body = client.get("/api/v1/health-status").json()
    assert body["lastPollAt"] is None and body["pollStale"] is True and body["newestBar"] is None


# ── R3: indicators per ticker ─────────────────────────────────────────────────

def _bars(n):
    from datetime import date, timedelta
    from market_data.bars import Bar

    out, day, price = [], date(2024, 1, 1), 100.0
    while len(out) < n:
        if day.weekday() < 5:
            price *= 1.001 if len(out) % 3 else 0.998
            out.append(Bar(day.isoformat(), price * 1.01, price * 0.99, price, price * 0.999, 1_000_000 + len(out), price))
        day += timedelta(days=1)
    return out


def test_indicators_route_gives_latest_values(client, sqlite_api):
    from market_data.bar_store import BarStore

    BarStore(sqlite_api).upsert("AAPL", _bars(300))
    body = client.get("/api/v1/indicators/aapl").json()
    assert body["ticker"] == "AAPL" and body["bars"] == 300 and body["asOf"]
    v = body["values"]
    assert v["sma200"] is not None and v["rsi14"] is not None and v["adx14"] is not None and v["obv"] is not None
    assert body["warmupMissing"] == []  # 300 bars warm up everything, the 252-bar percentiles included


def test_indicators_route_reports_warm_up_on_short_history(client, sqlite_api):
    from market_data.bar_store import BarStore

    BarStore(sqlite_api).upsert("NEW", _bars(60))
    body = client.get("/api/v1/indicators/NEW").json()
    assert body["values"]["sma200"] is None and "sma200" in body["warmupMissing"]
    assert body["values"]["sma50"] is not None


def test_indicators_route_404_and_bad_ticker(client, sqlite_api):
    assert client.get("/api/v1/indicators/ZZZZ").status_code == 404
    assert client.get("/api/v1/indicators/bad%20ticker!").status_code == 422
