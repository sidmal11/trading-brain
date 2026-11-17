import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.storage import SqliteStore  # noqa: E402


def store(tmp_path):
    return SqliteStore(str(tmp_path / "a.db"))


def test_saved_alert_carries_price_strategy_and_date(tmp_path):
    s = store(tmp_path)
    s.save_alert("a1", "NVDA", "sig", "pros", price=182.4, trading_date="2026-11-16")
    alert = s.get_alert("a1")
    assert alert["priceAtAlert"] == 182.4
    assert alert["strategy"] == "volume"
    assert alert["tradingDate"] == "2026-11-16"


def test_no_price_means_no_price_field(tmp_path):
    s = store(tmp_path)
    s.save_alert("a1", "NVDA", "sig", "pros", trading_date="2026-11-16")
    assert "priceAtAlert" not in s.get_alert("a1")


def test_alert_exists_matches_ticker_strategy_and_date(tmp_path):
    s = store(tmp_path)
    s.save_alert("a1", "NVDA", "sig", "pros", trading_date="2026-11-16")
    assert s.alert_exists("NVDA", "volume", "2026-11-16") is True
    assert s.alert_exists("NVDA", "volume", "2026-11-17") is False
    assert s.alert_exists("NVDA", "macd", "2026-11-16") is False
    assert s.alert_exists("AAPL", "volume", "2026-11-16") is False


def test_alert_exists_survives_a_new_store_object(tmp_path):
    store(tmp_path).save_alert("a1", "NVDA", "sig", "pros", trading_date="2026-11-16")
    assert store(tmp_path).alert_exists("NVDA", "volume", "2026-11-16") is True


def test_legacy_row_without_new_fields_never_matches(tmp_path):
    import json, sqlite3
    s = store(tmp_path)
    with sqlite3.connect(s.path) as conn:
        conn.execute("INSERT INTO alerts (alert_id, doc) VALUES (?, ?)",
                     ("old", json.dumps({"alertId": "old", "ticker": "NVDA", "status": "PENDING"})))
    assert s.alert_exists("NVDA", "volume", "2026-11-16") is False
