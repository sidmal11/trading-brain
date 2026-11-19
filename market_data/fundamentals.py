"""Current fundamentals from Yahoo (R8; owner Q3 c, Q13 a). Current values, not point-in-time: shown with the
long-term score only, never used in backtests, outcomes or ML. Missing stays missing."""
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

FIELDS = ("revenueGrowth", "earningsGrowth", "grossMargins", "operatingMargins", "profitMargins", "returnOnEquity",
          "debtToEquity", "currentRatio", "freeCashflow", "trailingPE", "forwardPE", "priceToSalesTrailing12Months",
          "enterpriseToEbitda", "marketCap")
REFRESH_DAYS = 7
PER_PASS = 25  # Yahoo is asked about at most this many tickers in one daily pass


def _number(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and x == x else None


def download_fundamentals(ticker) -> dict | None:
    """{field: number or None} for one ticker, or None when Yahoo fails."""
    import yfinance as yf

    try:
        info = yf.Ticker(ticker).info
    except Exception as e:  # network, rate limit, unknown ticker
        logger.warning("Fundamentals download failed for %s: %s", ticker, e)
        return None
    values = {k: _number(info.get(k)) for k in FIELDS}
    if values["grossMargins"] == 0.0:  # Yahoo reports 0 for banks and others where it does not apply
        values["grossMargins"] = None
    return values


class FundamentalsStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS fundamentals (ticker TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, "
                         "doc TEXT NOT NULL)")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def save(self, ticker, values, fetched_at=None):
        with self._lock, self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO fundamentals VALUES (?, ?, ?)",
                         (ticker, fetched_at or datetime.now(timezone.utc).isoformat(), json.dumps(values)))

    def get(self, tickers) -> dict:
        """{ticker: {"values", "fetchedAt"}} for the stored ones."""
        if not tickers:
            return {}
        with self._connect() as conn:
            rows = conn.execute(f"SELECT ticker, fetched_at, doc FROM fundamentals WHERE ticker IN "
                                f"({','.join('?' * len(tickers))})", list(tickers)).fetchall()
        return {t: {"values": json.loads(doc), "fetchedAt": at} for t, at, doc in rows}

    def due(self, tickers, now=None) -> list:
        """Tickers with no values or values older than REFRESH_DAYS, oldest first, at most PER_PASS."""
        now = now or datetime.now(timezone.utc)
        have = self.get(tickers)
        missing = [t for t in tickers if t not in have]
        stale = sorted((have[t]["fetchedAt"], t) for t in have
                       if now - datetime.fromisoformat(have[t]["fetchedAt"]) > timedelta(days=REFRESH_DAYS))
        return (missing + [t for _, t in stale])[:PER_PASS]


def refresh_fundamentals(store, tickers, download=None) -> int:
    """Download the due tickers; a failure keeps the old values and their old fetch time. Returns how many saved."""
    download, saved = download or download_fundamentals, 0
    for t in store.due(tickers):
        values = download(t)
        if values is not None:
            store.save(t, values)
            saved += 1
    return saved
