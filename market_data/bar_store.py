"""Completed daily bars and the latest data-quality report per ticker, in the main SQLite file.

The poll process writes (market_data.refresh); the api process and strategies read.
"""
import json
import sqlite3
import threading
from datetime import datetime, timezone

from market_data.bars import Bar

_COLS = "date, high, low, close, open, volume, adj_close"


class BarStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS market_bars (ticker TEXT NOT NULL, date TEXT NOT NULL, "
                         "open REAL, high REAL, low REAL, close REAL, adj_close REAL, volume REAL, "
                         "fetched_at TEXT NOT NULL, PRIMARY KEY (ticker, date))")
            conn.execute("CREATE TABLE IF NOT EXISTS data_quality_reports (ticker TEXT PRIMARY KEY, "
                         "checked_at TEXT NOT NULL, status TEXT NOT NULL, issues TEXT NOT NULL)")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def _write(self, conn, ticker, bars):
        now = datetime.now(timezone.utc).isoformat()
        conn.executemany(f"INSERT OR REPLACE INTO market_bars (ticker, {_COLS}, fetched_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [(ticker, *b, now) for b in bars])

    def upsert(self, ticker, bars) -> int:
        with self._lock, self._connect() as conn:
            self._write(conn, ticker, bars)
        return len(bars)

    def replace(self, ticker, bars):
        """All of a ticker's rows become `bars` in one transaction (history re-adjusted after a split)."""
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM market_bars WHERE ticker = ?", (ticker,))
            self._write(conn, ticker, bars)

    def daily(self, tickers, before: str) -> dict:
        """{ticker: [Bar]} oldest first, dated before `before`; tickers with no rows are absent."""
        tickers = list(tickers)
        if not tickers:
            return {}
        marks = ",".join("?" * len(tickers))
        with self._connect() as conn:
            rows = conn.execute(f"SELECT ticker, {_COLS} FROM market_bars WHERE ticker IN ({marks}) AND date < ? "
                                "ORDER BY ticker, date", (*tickers, before)).fetchall()
        out = {}
        for ticker, *values in rows:
            out.setdefault(ticker, []).append(Bar(*values))
        return out

    def newest_date(self, ticker) -> str | None:
        with self._connect() as conn:
            return conn.execute("SELECT MAX(date) FROM market_bars WHERE ticker = ?", (ticker,)).fetchone()[0]

    def save_report(self, ticker, report):
        with self._lock, self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO data_quality_reports VALUES (?, ?, ?, ?)",
                         (ticker, datetime.now(timezone.utc).isoformat(), report["status"],
                          json.dumps(report["issues"])))

    def reports(self) -> list:
        with self._connect() as conn:
            rows = conn.execute("SELECT ticker, checked_at, status, issues FROM data_quality_reports "
                                "ORDER BY ticker").fetchall()
        return [{"ticker": t, "checkedAt": c, "status": s, "issues": json.loads(i)} for t, c, s, i in rows]

    def report(self, ticker) -> dict | None:
        return next((r for r in self.reports() if r["ticker"] == ticker), None)
