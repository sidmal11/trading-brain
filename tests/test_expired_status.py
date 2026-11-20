"""EXPIRED is a stored status, never a decision; expired alerts cannot be bought."""

import contextlib
import importlib.util
import sys
from pathlib import Path

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api import main as api_main  # noqa: E402
from api import trading  # noqa: E402
from config import Config  # noqa: E402
from utils import telegram_client as tc  # noqa: E402
from utils.settings_store import SettingsStore  # noqa: E402
from utils.storage import SqliteStore, alert_store  # noqa: E402
from utils.trades_store import TradesStore  # noqa: E402

TODAY = "2026-11-17"
REASON = "Cleared at upgrade."


@pytest.fixture(params=["dynamodb", "sqlite"])
def db(request, monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setattr(Config, "STORAGE_BACKEND", request.param)
    monkeypatch.setattr(Config, "SQLITE_PATH", str(tmp_path / "trading.db"))
    monkeypatch.setattr(Config, "DYNAMODB_TABLE_NAME", "TradingAlerts")
    ctx = mock_aws() if request.param == "dynamodb" else contextlib.nullcontext()
    with ctx:
        if request.param == "dynamodb":
            boto3.resource("dynamodb", region_name="us-east-1").create_table(
                TableName="TradingAlerts", KeySchema=[{"AttributeName": "alertId", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "alertId", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST")
        yield alert_store()


def _save(db, alert_id, ticker="AAPL"):
    db.save_alert(alert_id, ticker, "Volume 3.4x", "Pros", trading_date=TODAY)


def test_expire_alert_sets_status_reason_and_time(db):
    _save(db, "a1")
    assert db.expire_alert("a1", REASON) is True
    alert = db.get_alert("a1")
    assert (alert["status"], alert["expiredReason"]) == ("EXPIRED", REASON)
    assert alert["expiredAt"].startswith("20")


def test_expire_alert_ignores_decided_alerts(db):
    _save(db, "a1")
    _save(db, "a2", "MSFT")
    db.update_alert_status("a1", "APPROVED")
    db.update_alert_status("a2", "REJECTED")
    assert db.expire_alert("a1", REASON) is False
    assert db.expire_alert("a2", REASON) is False
    assert db.expire_alert("missing", REASON) is False
    assert db.get_alert("a1")["status"] == "APPROVED"
    assert "expiredReason" not in db.get_alert("a2")


class _NoopTelegram:
    def update_message_decision(self, *args, **kwargs):
        pass


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(Config, "SQLITE_PATH", str(tmp_path / "s.db"))
    monkeypatch.setattr(Config, "ALLOW_UNAUTHENTICATED", True)
    monkeypatch.setattr(Config, "AUTH_MODE", "")
    monkeypatch.setattr(api_main, "_db_client", None)
    monkeypatch.setattr(api_main, "TelegramClient", _NoopTelegram)
    trades = TradesStore(str(tmp_path / "trades.db"))
    monkeypatch.setattr(trading, "get_trades_store", lambda: trades)
    monkeypatch.setattr(trading, "price_of", lambda ticker: 182.4)
    monkeypatch.setattr(trading, "session_fraction", lambda: 0.5)
    monkeypatch.setattr(trading, "trading_date", lambda: TODAY)
    SettingsStore(Config.SQLITE_PATH).put({
        "pollIntervalMinutes": 5, "volumeThreshold": 3.0, "maxCandidatesPerRun": 5,
        "telegramAlertsEnabled": True, "markets": ["us_stocks"],
        "tradingEnabled": True, "dollarsPerTrade": 500, "maxOrdersPerDay": 3})
    store = SqliteStore(Config.SQLITE_PATH)
    _save(store, "a1")
    store.expire_alert("a1", REASON)
    return {"client": TestClient(api_main.create_app()), "trades": trades, "store": store}


def test_review_route_rejects_expired_as_a_decision(env):
    r = env["client"].post("/api/v1/alerts/a1/review", json={"decision": "EXPIRED"})
    assert r.status_code == 422


@pytest.mark.parametrize("trading_on", [True, False])
def test_approving_an_expired_alert_is_refused(env, monkeypatch, trading_on):
    if not trading_on:
        SettingsStore(Config.SQLITE_PATH).put({
            "pollIntervalMinutes": 5, "volumeThreshold": 3.0, "maxCandidatesPerRun": 5,
            "telegramAlertsEnabled": True, "markets": ["us_stocks"],
            "tradingEnabled": False, "dollarsPerTrade": 500, "maxOrdersPerDay": 3})
    r = env["client"].post("/api/v1/alerts/a1/review", json={"decision": "APPROVED"})
    assert r.status_code == 409
    assert r.json()["detail"] == "Request expired: Cleared at upgrade."
    assert env["trades"].list_orders(trading.account_for("local-development")["id"]) == []
    assert env["store"].get_alert("a1")["status"] == "EXPIRED"


def test_clear_pending_expires_all_pending_and_is_idempotent(db, monkeypatch, capsys):
    _save(db, "p1")
    _save(db, "p2", "MSFT")
    _save(db, "ok", "NVDA")
    _save(db, "no", "TSLA")
    db.update_alert_status("ok", "APPROVED")
    db.update_alert_status("no", "REJECTED")
    def boom(*a, **k):
        raise AssertionError("Telegram must not be called")
    monkeypatch.setattr(tc.requests, "post", boom)
    spec = importlib.util.spec_from_file_location("clear_pending", ROOT / "scripts" / "clear_pending.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    script.main()
    assert capsys.readouterr().out.strip() == "Cleared 2 pending request(s)."
    script.main()
    assert capsys.readouterr().out.strip() == "Cleared 0 pending request(s)."
    statuses = {a["alertId"]: a["status"] for a in db.list_alerts()}
    assert statuses == {"p1": "EXPIRED", "p2": "EXPIRED", "ok": "APPROVED", "no": "REJECTED"}
