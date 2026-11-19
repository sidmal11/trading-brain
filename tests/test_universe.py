"""P3 universe: the seed, editing through the API, exchanges, bars and opportunities with domains."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import main as api_main  # noqa: E402
from config import Config  # noqa: E402
from market_data.bar_store import BarStore  # noqa: E402
from market_data.bars import Bar  # noqa: E402
from utils import universe  # noqa: E402
from utils.universe import SEED, SEED_TICKERS, UniverseStore, fill_exchanges, universe_tickers  # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(Config, "SQLITE_PATH", str(tmp_path / "u.db"))
    monkeypatch.setattr(Config, "AUTH_MODE", "")
    monkeypatch.setattr(Config, "ALLOW_UNAUTHENTICATED", True)
    return TestClient(api_main.app)


def test_seed_is_the_owners_table_75_tickers_in_16_domains():
    assert len(SEED) == 16 and len(SEED_TICKERS) == 75 and len(set(SEED_TICKERS)) == 75  # the spec says 77 but its table lists 75


def test_store_seeds_once_and_keeps_edits(tmp_path):
    path = str(tmp_path / "u.db")
    assert [x["ticker"] for x in UniverseStore(path).items()] == SEED_TICKERS
    UniverseStore(path).replace([{"ticker": "AAPL", "domain": "Mine"}])
    assert UniverseStore(path).items() == [{"ticker": "AAPL", "domain": "Mine", "exchange": None}]


def test_replace_keeps_known_exchanges(tmp_path):
    store = UniverseStore(str(tmp_path / "u.db"))
    store.set_exchange("AAPL", "NASDAQ")
    items = store.replace([{"ticker": "AAPL", "domain": "X"}, {"ticker": "ZZZ", "domain": "X"}])
    assert items == [{"ticker": "AAPL", "domain": "X", "exchange": "NASDAQ"}, {"ticker": "ZZZ", "domain": "X", "exchange": None}]


def test_universe_tickers_falls_back_to_the_seed_without_sqlite(monkeypatch):
    monkeypatch.setattr(Config, "STORAGE_BACKEND", "dynamodb")
    assert universe_tickers() == SEED_TICKERS


def test_fill_exchanges_stores_found_ones_and_leaves_unknown_empty(tmp_path):
    store = UniverseStore(str(tmp_path / "u.db"))
    store.replace([{"ticker": t, "domain": "X"} for t in ("AAPL", "JPM", "NOPE")])
    found = fill_exchanges(store, {"AAPL": "NASDAQ", "JPM": "NYSE"}.get)
    assert found == 2 and [x["exchange"] for x in store.items()] == ["NASDAQ", "NYSE", None]


def test_lookup_exchange_maps_codes_and_survives_errors(monkeypatch):
    monkeypatch.undo()  # drop the conftest stub: this test patches Yahoo itself
    real = universe.lookup_exchange

    class FakeTicker:
        def __init__(self, t):
            if t == "BOOM":
                raise RuntimeError("rate limited")
            self.fast_info = {"exchange": {"NVDA": "NMS", "JPM": "NYQ", "SPY": "PCX"}.get(t, "XXX")}

    monkeypatch.setattr("yfinance.Ticker", FakeTicker)
    assert [real(t) for t in ("NVDA", "JPM", "SPY", "ODD", "BOOM")] == ["NASDAQ", "NYSE", "AMEX", None, None]


def test_get_and_put_universe(client):
    body = client.get("/api/v1/universe").json()
    assert body["persistent"] and len(body["items"]) == 75 and body["items"][0] == {"ticker": "CRWV", "domain": "Neocloud", "exchange": None}
    r = client.put("/api/v1/universe", json={"items": [{"ticker": "aapl", "domain": " Mine "}, {"ticker": "BRK.B", "domain": "Mine"}]})
    assert r.status_code == 200 and [x["ticker"] for x in r.json()["items"]] == ["AAPL", "BRK.B"]
    assert r.json()["items"][0]["domain"] == "Mine"
    assert universe_tickers() == ["AAPL", "BRK.B"]


@pytest.mark.parametrize("items, reason", [
    ([{"ticker": f"T{i}", "domain": "X"} for i in range(101)], "at most 100 tickers"),
    ([{"ticker": "AAPL", "domain": "X"}, {"ticker": "aapl", "domain": "Y"}], "must not repeat: AAPL"),
    ([{"ticker": "BAD TICKER", "domain": "X"}], "pattern"),
    ([{"ticker": "AAPL", "domain": ""}], "at least 1"),
    ([], "at least 1"),
])
def test_put_universe_refuses_bad_lists(client, items, reason):
    r = client.put("/api/v1/universe", json={"items": items})
    assert r.status_code == 422 and reason in r.text
    assert len(client.get("/api/v1/universe").json()["items"]) == 75  # nothing stored


def test_put_universe_needs_sqlite(client, monkeypatch):
    monkeypatch.setattr(Config, "STORAGE_BACKEND", "dynamodb")
    r = client.put("/api/v1/universe", json={"items": [{"ticker": "AAPL", "domain": "X"}]})
    assert r.status_code == 503


def test_bars_route_serves_the_last_days(client):
    rows = [Bar(f"2026-01-{d:02d}", 11.0, 9.0, 10.0 + d, 10.0, 1000, 10.0 + d) for d in range(2, 30)]
    BarStore(Config.SQLITE_PATH).upsert("AAPL", rows)
    got = client.get("/api/v1/bars/aapl?days=5").json()
    assert [r["date"] for r in got] == [f"2026-01-{d:02d}" for d in range(25, 30)]
    assert got[-1] == {"date": "2026-01-29", "open": 10.0, "high": 11.0, "low": 9.0, "close": 39.0, "volume": 1000}
    assert client.get("/api/v1/bars/NOPE").status_code == 404


def test_opportunities_carry_domain_and_exchange(client, monkeypatch):
    UniverseStore(Config.SQLITE_PATH).set_exchange("NVDA", "NASDAQ")
    rows = [{"ticker": "NVDA", "eligible": True, "qualityScore": 70}, {"ticker": "OLD", "eligible": True, "qualityScore": 60}]
    monkeypatch.setattr(api_main, "_opportunities", lambda: rows)
    got = client.get("/api/v1/opportunities").json()
    assert (got[0]["domain"], got[0]["exchange"]) == ("AI chips and semiconductors", "NASDAQ")
    assert (got[1]["domain"], got[1]["exchange"]) == (None, None)  # dropped from the universe since scoring


def test_every_seeded_stock_compares_with_a_stored_sector_fund():
    from features.relative_strength import SECTOR_ETF
    from utils.api_client import REFERENCE

    stocks = [t for d, ts in SEED.items() if d != "Market ETFs" for t in ts]
    assert [t for t in stocks if t not in SECTOR_ETF] == []
    assert {SECTOR_ETF[t] for t in stocks} <= set(REFERENCE)
