"""Home-page top stocks: grouped quotes, failed lookups shown as None, and the route."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import main as api_main  # noqa: E402
from api import market_source, top_stocks  # noqa: E402
from config import Config  # noqa: E402


@pytest.fixture
def fake_quotes(monkeypatch):
    prices = {"NVDA": {"price": 120.5, "changePercent": 1.25}, "SPY": {"price": 510.0, "changePercent": -0.4}}
    monkeypatch.setattr(market_source, "index_quote", lambda symbol: prices.get(symbol))
    return prices


def test_groups_keep_their_order_and_names():
    assert [name for name, _ in top_stocks.TOP_STOCK_GROUPS] == [
        "Tech stocks", "Market ETFs", "Crypto", "Energy and gold",
    ]


def test_quotes_are_attached_to_each_symbol(fake_quotes):
    groups = top_stocks.top_stock_groups()
    tech = groups[0]
    assert tech["name"] == "Tech stocks"
    nvda = next(item for item in tech["items"] if item["symbol"] == "NVDA")
    assert nvda == {"symbol": "NVDA", "price": 120.5, "changePercent": 1.25}


def test_failed_lookup_is_none_not_an_error(fake_quotes):
    groups = top_stocks.top_stock_groups()
    amd = next(item for item in groups[0]["items"] if item["symbol"] == "AMD")
    assert amd == {"symbol": "AMD", "price": None, "changePercent": None}


def test_route_returns_every_group(monkeypatch, fake_quotes, tmp_path):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setattr(Config, "STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(Config, "SQLITE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(Config, "AUTH_MODE", "")
    monkeypatch.setattr(api_main, "_db_client", None)
    monkeypatch.setattr(api_main, "_subscriber_store", None)
    client = TestClient(api_main.create_app())

    body = client.get("/api/top-stocks").json()
    assert [group["name"] for group in body] == [name for name, _ in top_stocks.TOP_STOCK_GROUPS]


def test_placeholder_row_without_close_is_skipped(monkeypatch):
    # Regression: Yahoo's placeholder row for today has a NaN close, which broke the JSON response.
    import pandas as pd
    from types import SimpleNamespace

    frame = pd.DataFrame({"Close": [100.0, 110.0, float("nan")]})
    fake_yf = SimpleNamespace(Ticker=lambda symbol: SimpleNamespace(history=lambda **kwargs: frame))
    monkeypatch.setattr(market_source, "_yfinance", lambda: fake_yf)
    assert market_source._fetch_index("NVDA") == {"price": 110.0, "changePercent": 10.0}
