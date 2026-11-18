"""Next earnings dates from Yahoo, and the warning shown when one is close.

Yahoo can still list a date that has passed (it rolls forward later), so only dates on or after today
count; anything else is unknown and shows nothing. Whether a report is before or after the market is
not known here. The warning never blocks a request (spec 2026-11-18).
"""
import logging
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone

import yfinance as yf

from market_data.calendar import is_trading_day

logger = logging.getLogger(__name__)

WARN_TRADING_DAYS = 3
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _day(d):
    return d.date() if isinstance(d, datetime) else d


def next_earnings(dates, today: date) -> date | None:
    """The earliest listed date on or after `today`, or None when none is listed."""
    upcoming = [_day(d) for d in dates or [] if isinstance(d, (date, datetime)) and _day(d) >= today]
    return min(upcoming) if upcoming else None


def trading_days_until(day: date, today: date) -> int:
    """Trading days after today up to and including `day`; 0 when `day` is today."""
    count, d = 0, today
    while d < day:
        d += timedelta(days=1)
        count += is_trading_day(d)
    return count


def earnings_note(day: date | None, today: date) -> str | None:
    """`Earnings in N days (Mon D)` when the next report is within 3 trading days; otherwise None."""
    if day is None or day < today:
        return None
    n = trading_days_until(day, today)
    if n > WARN_TRADING_DAYS:
        return None
    when = "today" if n == 0 else f"in {n} day{'s' if n != 1 else ''}"
    return f"Earnings {when} ({_MONTHS[day.month - 1]} {day.day})"


def download_earnings(tickers, today: date) -> dict:
    """{ticker: next date or None}; a ticker whose lookup fails is left out, so its stored date stays."""
    out = {}
    for t in tickers:
        try:
            calendar = yf.Ticker(t).calendar or {}
        except Exception as e:  # ETFs raise "no fundamentals"; an outage raises too
            logger.info("No earnings calendar for %s: %s", t, type(e).__name__)
            continue
        out[t] = next_earnings(calendar.get("Earnings Date"), today)
    return out


class EarningsStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS earnings_dates (ticker TEXT PRIMARY KEY, next_date TEXT, "
                         "fetched_at TEXT NOT NULL)")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def save(self, dates: dict):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.executemany("INSERT OR REPLACE INTO earnings_dates VALUES (?, ?, ?)",
                             [(t, d.isoformat() if d else None, now) for t, d in dates.items()])

    def get(self, tickers) -> dict:
        tickers = list(tickers)
        if not tickers:
            return {}
        with self._connect() as conn:
            rows = conn.execute(f"SELECT ticker, next_date FROM earnings_dates WHERE ticker IN "
                                f"({','.join('?' * len(tickers))})", tickers).fetchall()
        return {t: date.fromisoformat(d) if d else None for t, d in rows}
