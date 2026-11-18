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
