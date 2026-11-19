"""Test-run defaults. Importing api.main builds the app, which refuses to start with no login configured."""

import os

os.environ.setdefault("ALLOW_UNAUTHENTICATED", "true")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_exchange_lookups(monkeypatch):
    """The poll looks up exchanges on Yahoo; tests never reach the network."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import utils.universe

    monkeypatch.setattr(utils.universe, "lookup_exchange", lambda ticker: None)
