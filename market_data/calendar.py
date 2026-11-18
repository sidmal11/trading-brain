"""NYSE regular-session calendar for 2026 to 2028: holidays and 13:00 ET half days.

Source: nyse.com "Holidays & Trading Hours", read 2026-11-18. Extend the tables each year;
tests/test_market_calendar.py fails once they cover less than the next 60 days. Past years are not
listed: gaps in stored history are found against SPY's own dates instead (data_quality.checks).
"""
from datetime import date, timedelta

COVERED_UNTIL = date(2028, 12, 31)
FULL_SESSION_MINUTES = 390
HALF_SESSION_MINUTES = 210

HOLIDAYS = frozenset(date.fromisoformat(d) for d in (
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
    "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
    "2027-09-06", "2027-11-25", "2027-12-24",
    "2028-01-17", "2028-02-21", "2028-04-14", "2028-05-29", "2028-06-19", "2028-07-04", "2028-09-04",
    "2028-11-23", "2028-12-25",
))
HALF_DAYS = frozenset(date.fromisoformat(d) for d in (
    "2026-11-27", "2026-12-24", "2027-11-26", "2028-07-03", "2028-11-24",
))


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def session_minutes(d: date) -> int | None:
    """Length of the regular session in minutes, or None when the market is closed all day."""
    if not is_trading_day(d):
        return None
    return HALF_SESSION_MINUTES if d in HALF_DAYS else FULL_SESSION_MINUTES


def previous_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d
