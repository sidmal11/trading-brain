"""GET /api/v1/performance and the cached Yahoo daily bars. No network."""

import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import main as api_main  # noqa: E402
from api import performance, trading  # noqa: E402
from config import Config  # noqa: E402
from utils.storage import SqliteStore  # noqa: E402
from utils.trades_store import TradesStore  # noqa: E402

BARS = {"NVDA": [("2026-11-17", 154.0, 150.0, 153.0), ("2026-11-18", 158.2, 152.0, 156.0)]}


@pytest.fixture(autouse=True)
def _clear_cache():
    performance._bars_cache.clear()


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(Config, "SQLITE_PATH", str(tmp_path / "s.db"))
    monkeypatch.setattr(Config, "ALLOW_UNAUTHENTICATED", True)
    monkeypatch.setattr(Config, "AUTH_MODE", "")
    monkeypatch.setattr(api_main, "_db_client", None)
    trades = TradesStore(str(tmp_path / "trades.db"))
    monkeypatch.setattr(trading, "get_trades_store", lambda: trades)
    monkeypatch.setattr(trading, "price_of", lambda t: 156.67)
    monkeypatch.setattr(performance, "daily_bars", lambda tickers, start: BARS)
    order, _ = trades.claim_buy(trading.default_account_id(trades), "a1", "NVDA", 3, "2026-11-17")
    trades.fill(order["id"], 153.7)
    alerts = SqliteStore(Config.SQLITE_PATH)
    alerts.save_alert("a1", "NVDA", "m", "p", price=150.0, trading_date="2026-11-17")
    return TestClient(api_main.create_app())


def test_performance_route_shape(env):
    body = env.get("/api/v1/performance").json()
    assert set(body) == {"broker", "totals", "trades", "recommendations"}
    assert body["broker"] == "mock"
    t = body["trades"][0]
    assert (t["ticker"], t["open"], t["exitPrice"], t["pnl"]) == ("NVDA", True, 156.0, 6.9)
    assert (t["bestHigh"], t["bestHighDate"], t["bestExitPnl"]) == (158.2, "2026-11-18", 13.5)
    assert body["totals"]["unrealizedPnl"] == 6.9
    assert body["recommendations"]["items"][0]["bestPrice"] == 158.2


def test_performance_off_sqlite_has_no_trades_but_has_recommendations(env, monkeypatch):
    monkeypatch.setattr(Config, "STORAGE_BACKEND", "dynamodb")
    monkeypatch.setattr(api_main, "get_db", lambda: SqliteStore(Config.SQLITE_PATH))
    body = env.get("/api/v1/performance").json()
    assert body["trades"] == [] and body["totals"]["moneyIn"] == 0
    assert body["recommendations"]["items"][0]["ticker"] == "NVDA"


def test_performance_with_yahoo_down_returns_nulls_not_500(env, monkeypatch):
    monkeypatch.setattr(performance, "daily_bars", lambda tickers, start: {})
    monkeypatch.setattr(trading, "price_of", lambda t: None)
    r = env.get("/api/v1/performance")
    assert r.status_code == 200
    t = r.json()["trades"][0]
    assert t["pnl"] is None and t["bestHigh"] is None


def _frame(single=False):
    idx = pd.to_datetime(["2026-11-17", "2026-11-18", "2026-11-19"])
    data = {"High": [2.0, float("nan"), 4.0], "Low": [1.0, float("nan"), 3.0], "Close": [1.5, float("nan"), 3.5]}
    if single:
        return pd.DataFrame(data, index=idx)
    return pd.DataFrame({("AAA", k): v for k, v in data.items()}, index=idx)


def test_daily_bars_parses_frame_and_skips_nan(monkeypatch):
    monkeypatch.setattr(performance.yf, "download", lambda *a, **k: _frame())
    assert performance.daily_bars(["AAA", "MISSING"], "2026-11-17") == {
        "AAA": [("2026-11-17", 2.0, 1.0, 1.5), ("2026-11-19", 4.0, 3.0, 3.5)]}


def test_daily_bars_single_ticker_without_ticker_level(monkeypatch):
    monkeypatch.setattr(performance.yf, "download", lambda *a, **k: _frame(single=True))
    assert performance.daily_bars(["AAA"], "2026-11-17")["AAA"][1] == ("2026-11-19", 4.0, 3.0, 3.5)


def test_daily_bars_failure_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(performance.yf, "download", boom)
    assert performance.daily_bars(["AAA"], "2026-11-17") == {}


def test_daily_bars_caches_for_five_minutes(monkeypatch):
    calls = []
    monkeypatch.setattr(performance.yf, "download", lambda *a, **k: calls.append(1) or _frame())
    performance.daily_bars(["AAA"], "2026-11-17")
    performance.daily_bars(["AAA"], "2026-11-17")
    assert len(calls) == 1


def test_price_now_comes_from_the_newest_bar_close(env, monkeypatch):
    def boom(t):
        raise AssertionError("price_of must not be called")

    monkeypatch.setattr(trading, "price_of", boom)
    body = env.get("/api/v1/performance").json()
    assert body["trades"][0]["exitPrice"] == 156.0
    assert body["recommendations"]["items"][0]["priceNow"] == 156.0


def test_route_never_calls_price_of(env, monkeypatch):
    def boom(t):
        raise AssertionError("price_of must not be called")

    monkeypatch.setattr(trading, "price_of", boom)
    monkeypatch.setattr(performance, "daily_bars", lambda tickers, start: {})
    body = env.get("/api/v1/performance").json()
    t = body["trades"][0]
    assert t["exitPrice"] is None and t["priceAsOf"] is None
    assert body["totals"]["currentValue"] is None and body["totals"]["unrealizedPnl"] is None


def test_route_reads_every_alert(env):
    store = SqliteStore(Config.SQLITE_PATH)
    for i in range(201):
        store.save_alert(f"x{i}", "NVDA", "m", "p", price=150.0, trading_date="2026-11-17")
    assert len(env.get("/api/v1/performance").json()["recommendations"]["items"]) == 202


def test_route_requests_only_scored_and_filled_tickers(env, monkeypatch):
    asked = []
    monkeypatch.setattr(performance, "daily_bars", lambda tickers, start: asked.append((sorted(tickers), start)) or {})
    store = SqliteStore(Config.SQLITE_PATH)
    store.save_alert("old", "OLD", "m", "p")  # no price or date: not scored
    store.save_alert("nodate", "NOD", "m", "p", price=1.0)
    trading.get_trades_store().claim_buy(trading.default_account_id(trading.get_trades_store()), "a9", "PND", 1, "2026-11-01")  # pending, never filled
    env.get("/api/v1/performance")
    assert asked == [(["NVDA"], "2026-11-17")]


def test_daily_bars_downloads_with_threads(monkeypatch):
    seen = {}
    monkeypatch.setattr(performance.yf, "download", lambda *a, **k: seen.update(k) or _frame())
    performance.daily_bars(["AAA", "BBB"], "2026-11-17")
    assert seen["threads"] is True


def test_all_nan_result_is_cached_for_60s_only(monkeypatch):
    idx = pd.to_datetime(["2026-11-17"])
    nan_frame = pd.DataFrame({("AAA", k): [float("nan")] for k in ("High", "Low", "Close")}, index=idx)
    calls, now = [], [1000.0]
    monkeypatch.setattr(performance.yf, "download", lambda *a, **k: calls.append(1) or nan_frame)
    monkeypatch.setattr(performance.time, "monotonic", lambda: now[0])
    assert performance.daily_bars(["AAA"], "2026-11-17") == {}
    now[0] += 59
    performance.daily_bars(["AAA"], "2026-11-17")
    assert len(calls) == 1
    now[0] += 2
    performance.daily_bars(["AAA"], "2026-11-17")
    assert len(calls) == 2


def test_failed_download_is_not_retried_within_60s(monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise RuntimeError("down")

    monkeypatch.setattr(performance.yf, "download", boom)
    assert performance.daily_bars(["AAA"], "2026-11-17") == {}
    assert performance.daily_bars(["AAA"], "2026-11-17") == {}
    assert len(calls) == 1


def test_all_nan_ticker_is_absent(monkeypatch):
    idx = pd.to_datetime(["2026-11-17"])
    nan = [float("nan")]
    frame = pd.DataFrame({(t, k): v for t in ("AAA", "BBB") for k, v in
                          (("High", nan), ("Low", nan), ("Close", nan) if t == "AAA" else ("Close", [1.0]))},
                         index=idx)
    frame[("BBB", "High")], frame[("BBB", "Low")] = 2.0, 1.0
    monkeypatch.setattr(performance.yf, "download", lambda *a, **k: frame)
    assert list(performance.daily_bars(["AAA", "BBB"], "2026-11-17")) == ["BBB"]
