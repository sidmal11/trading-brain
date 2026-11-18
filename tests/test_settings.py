"""Settings: validation, persistence, and the poll honouring each one."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lambda_function  # noqa: E402
from api import main as api_main  # noqa: E402
from config import Config  # noqa: E402
from utils import telegram_client as tc  # noqa: E402
from utils.settings_store import SettingsStore, defaults  # noqa: E402
from utils.storage import alert_store, subscriber_store  # noqa: E402

GOOD = {
    "pollIntervalMinutes": 10,
    "volumeThreshold": 2.5,
    "maxCandidatesPerRun": 3,
    "telegramAlertsEnabled": True,
    "markets": ["us_stocks"],
    "tradingEnabled": False,
    "dollarsPerTrade": 500,
    "maxOrdersPerDay": 3,
    "maxChasePct": 3,
    "riskPerTrade": 50,
    "stopAlertPct": 1,
}


@pytest.fixture
def app_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setattr(Config, "STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(Config, "SQLITE_PATH", str(tmp_path / "s.db"))
    monkeypatch.setattr(Config, "AUTH_MODE", "")
    monkeypatch.setattr(Config, "ALLOW_UNAUTHENTICATED", True)
    monkeypatch.setattr(api_main, "_db_client", None)
    monkeypatch.setattr(api_main, "_subscriber_store", None)
    return TestClient(api_main.create_app())


# ── API ───────────────────────────────────────────────────────────────────────

def test_defaults_are_served_before_anything_is_saved(app_env):
    body = app_env.get("/api/v1/settings").json()
    assert body["pollIntervalMinutes"] == 5
    assert body["persistent"] is True
    assert body["markets"] == ["us_stocks"]


def test_put_then_get_returns_the_saved_values(app_env):
    assert app_env.put("/api/v1/settings", json=GOOD).status_code == 200
    body = app_env.get("/api/v1/settings").json()
    assert body["pollIntervalMinutes"] == 10
    assert body["volumeThreshold"] == 2.5


@pytest.mark.parametrize("field, bad", [
    ("pollIntervalMinutes", 0),
    ("pollIntervalMinutes", 61),
    ("volumeThreshold", 0.1),
    ("volumeThreshold", 11),
    ("maxCandidatesPerRun", 0),
    ("maxCandidatesPerRun", 21),
])
def test_out_of_range_values_are_refused(app_env, field, bad):
    r = app_env.put("/api/v1/settings", json={**GOOD, field: bad})
    assert r.status_code == 422


def test_unknown_market_is_refused(app_env):
    r = app_env.put("/api/v1/settings", json={**GOOD, "markets": ["crypto"]})
    assert r.status_code == 422


def test_empty_market_list_is_refused(app_env):
    r = app_env.put("/api/v1/settings", json={**GOOD, "markets": []})
    assert r.status_code == 422


def test_duplicate_markets_are_refused(app_env):
    r = app_env.put("/api/v1/settings", json={**GOOD, "markets": ["us_stocks", "us_stocks"]})
    assert r.status_code == 422


def test_put_is_refused_on_dynamodb_backend(app_env, monkeypatch):
    monkeypatch.setattr(Config, "STORAGE_BACKEND", "dynamodb")
    r = app_env.put("/api/v1/settings", json=GOOD)
    assert r.status_code == 503


def test_settings_survive_a_new_store_instance(tmp_path):
    path = str(tmp_path / "persist.db")
    SettingsStore(path).put(GOOD)
    assert SettingsStore(path).get()["pollIntervalMinutes"] == 10


# ── Trading settings ──────────────────────────────────────────────────────────

def test_trading_defaults():
    d = defaults()
    assert d["tradingEnabled"] is False
    assert d["dollarsPerTrade"] == 500
    assert d["maxOrdersPerDay"] == 3


def test_old_stored_settings_get_trading_defaults(tmp_path):
    old = {k: v for k, v in GOOD.items() if k not in ("tradingEnabled", "dollarsPerTrade", "maxOrdersPerDay")}
    store = SettingsStore(str(tmp_path / "old.db"))
    store.put(old)
    got = store.get()
    assert (got["tradingEnabled"], got["dollarsPerTrade"], got["maxOrdersPerDay"]) == (False, 500, 3)


@pytest.mark.parametrize("field, bad", [
    ("dollarsPerTrade", 49),
    ("dollarsPerTrade", 5001),
    ("dollarsPerTrade", 500.5),
    ("maxOrdersPerDay", 0),
    ("maxOrdersPerDay", 21),
])
def test_put_settings_rejects_out_of_range_trading_values(app_env, field, bad):
    assert app_env.put("/api/v1/settings", json={**GOOD, field: bad}).status_code == 422


def test_chase_and_risk_defaults():
    d = defaults()
    assert (d["maxChasePct"], d["riskPerTrade"], d["stopAlertPct"]) == (3, 50, 1)


@pytest.mark.parametrize("field, bad", [
    ("maxChasePct", 0.4), ("maxChasePct", 21), ("riskPerTrade", 0), ("riskPerTrade", 5001),
    ("stopAlertPct", 0), ("stopAlertPct", 25)])
def test_put_settings_rejects_out_of_range_chase_and_risk(app_env, field, bad):
    assert app_env.put("/api/v1/settings", json={**GOOD, field: bad}).status_code == 422


def test_put_settings_round_trips_trading_values(app_env):
    new = {**GOOD, "tradingEnabled": True, "dollarsPerTrade": 750, "maxOrdersPerDay": 5}
    assert app_env.put("/api/v1/settings", json=new).status_code == 200
    body = app_env.get("/api/v1/settings").json()
    assert (body["tradingEnabled"], body["dollarsPerTrade"], body["maxOrdersPerDay"]) == (True, 750, 5)


def test_trades_path_defaults_next_to_sqlite_path():
    from config import default_trades_path
    assert Path(default_trades_path("/data/trading_brain.db")) == Path("/data/trades.db")


# ── Poll honours settings ─────────────────────────────────────────────────────

class FakeScan:
    def get_market_scan_results(self, market="US", session_fraction=1.0, trading_date=None):
        return [{"ticker": "AAA", "volume_score": 9}, {"ticker": "BBB", "volume_score": 3}, {"ticker": "CCC", "volume_score": 1}]

    def get_stock_details(self, ticker):
        return {}


class FakeLlm:
    def analyze_stock(self, *a, **k):
        return "analysis"


@pytest.fixture
def poll_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setattr(Config, "STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(Config, "SQLITE_PATH", str(tmp_path / "p.db"))
    monkeypatch.setattr(Config, "TRADES_SQLITE_PATH", str(tmp_path / "trades.db"))
    monkeypatch.setattr(Config, "TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", "1000")
    monkeypatch.setattr(Config, "ADMIN_CHAT_IDS", "")
    monkeypatch.setattr(lambda_function, "session_fraction", lambda: 0.5)
    monkeypatch.setattr(lambda_function, "trading_date", lambda: "2026-11-16")
    monkeypatch.setattr(lambda_function, "ApiClient", FakeScan)
    monkeypatch.setattr(lambda_function, "LlmClient", FakeLlm)
    monkeypatch.setattr(lambda_function, "completed_bars", lambda tickers, today: {})
    monkeypatch.setattr(lambda_function, "_macd_days", set())
    monkeypatch.setattr(lambda_function, "refresh_daily", lambda *a, **k: {"failed": False})  # no Yahoo
    monkeypatch.setattr(lambda_function, "_bar_days", set())
    monkeypatch.setattr(lambda_function, "_backtest_days", set())
    monkeypatch.setattr(lambda_function, "_bar_retry_at", 0.0)
    sent = []

    class Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "result": {"message_id": 1}}

    def post(url, json=None, timeout=None):
        sent.append(json)
        return Resp()

    monkeypatch.setattr(tc.requests, "post", post)
    return sent


def test_threshold_setting_controls_which_signals_are_stored(poll_env):
    SettingsStore(Config.SQLITE_PATH).put({**GOOD, "volumeThreshold": 5.0, "maxCandidatesPerRun": 20})
    lambda_function.lambda_handler({}, None)
    assert [a["ticker"] for a in alert_store().list_alerts()] == ["AAA"]


def test_candidate_cap_setting_limits_the_run(poll_env):
    SettingsStore(Config.SQLITE_PATH).put({**GOOD, "volumeThreshold": 0.5, "maxCandidatesPerRun": 1})
    lambda_function.lambda_handler({}, None)
    assert [a["ticker"] for a in alert_store().list_alerts()] == ["AAA"]


def test_disabling_us_stocks_skips_the_scan(poll_env):
    SettingsStore(Config.SQLITE_PATH).put({**GOOD, "markets": ["futures"]})
    result = lambda_function.lambda_handler({}, None)
    assert result["body"] == "US stocks disabled in settings."
    assert alert_store().list_alerts() == []


def test_telegram_off_stores_the_alert_but_sends_nothing(poll_env):
    SettingsStore(Config.SQLITE_PATH).put({**GOOD, "telegramAlertsEnabled": False, "volumeThreshold": 0.5})
    lambda_function.lambda_handler({}, None)
    assert alert_store().list_alerts()  # still in the dashboard queue
    assert poll_env == []  # nothing sent to Telegram


def test_defaults_match_config():
    assert defaults()["pollIntervalMinutes"] == Config.POLLING_INTERVAL_MINUTES


def test_browser_preflight_allows_put_for_settings(app_env):
    # Regression: PUT was missing from CORS, so the dashboard's save failed with "Failed to fetch".
    r = app_env.options(
        "/api/v1/settings",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "PUT"},
    )
    assert "PUT" in r.headers.get("access-control-allow-methods", "")


def test_stop_alert_distance_accepts_half_a_percent(app_env):
    assert app_env.put("/api/v1/settings", json={**GOOD, "stopAlertPct": 0.5}).status_code == 200
    assert app_env.get("/api/v1/settings").json()["stopAlertPct"] == 0.5
