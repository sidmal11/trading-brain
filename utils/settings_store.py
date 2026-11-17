"""User-editable settings, stored in the SQLite file.

Read by the poll on every run, so a change takes effect on the next run without a
restart. Settings need the SQLite backend: on AWS (DynamoDB) there is no file to
write, so reads fall back to the config defaults and writes are refused.

Defaults come from config.py; a value outside its range can never be stored.
"""

import json
import sqlite3
import threading

from config import Config

MARKET_US_STOCKS = "us_stocks"
MARKET_FUTURES = "futures"
MARKET_OPTIONS = "options"
ALL_MARKETS = (MARKET_US_STOCKS, MARKET_FUTURES, MARKET_OPTIONS)

# The poll only scans US stocks today. Futures and options are stored so the
# choice is kept, and the scanner will read them once those scanners exist.
SCANNED_MARKETS = (MARKET_US_STOCKS,)


def defaults() -> dict:
    return {
        "pollIntervalMinutes": Config.POLLING_INTERVAL_MINUTES,
        "volumeThreshold": Config.STRATEGY_VOLUME_THRESHOLD,
        "maxCandidatesPerRun": 5,
        "telegramAlertsEnabled": True,
        "markets": [MARKET_US_STOCKS],
        "tradingEnabled": False,
        "dollarsPerTrade": 500,
        "maxOrdersPerDay": 3,
    }


class SettingsStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def get(self) -> dict:
        """Effective settings: stored values over defaults."""
        merged = defaults()
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        for key, value in rows:
            if key in merged:
                merged[key] = json.loads(value)
        return merged

    def put(self, values: dict) -> dict:
        """Store a full settings object. Caller must have validated it."""
        with self._lock, self._connect() as conn:
            for key, value in values.items():
                conn.execute(
                    "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                    (key, json.dumps(value)),
                )
        return self.get()


def settings_store():
    """The settings store, or None on the DynamoDB backend (settings then cannot be saved)."""
    if Config.STORAGE_BACKEND == "sqlite":
        return SettingsStore(Config.SQLITE_PATH)
    return None


def effective_settings() -> dict:
    """What the poll should use: stored settings, or the defaults when no store is available."""
    store = settings_store()
    return store.get() if store else defaults()
