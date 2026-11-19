"""Test-run defaults. Importing api.main builds the app, which refuses to start with no login configured."""

import os

os.environ.setdefault("ALLOW_UNAUTHENTICATED", "true")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_exchange_lookups(monkeypatch):
    """The poll looks up exchanges and fundamentals on Yahoo; tests never reach the network."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import utils.universe

    monkeypatch.setattr(utils.universe, "lookup_exchange", lambda ticker: None)
    import market_data.fundamentals

    monkeypatch.setattr(market_data.fundamentals, "download_fundamentals", lambda ticker: None)


@pytest.fixture(autouse=True)
def _trades_file_in_tmp(monkeypatch, tmp_path):
    """No test writes trades.db into the repo, whichever route opens the trades store."""
    from config import Config

    monkeypatch.setattr(Config, "TRADES_SQLITE_PATH", str(tmp_path / "trades.db"))
