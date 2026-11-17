"""US equity regular-session clock (09:30-16:00 America/New_York, Mon-Fri)."""
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")
SESSION_OPEN = time(9, 30)
SESSION_MINUTES = 390
MIN_SESSION_FRACTION = 30 / SESSION_MINUTES


def _eastern(now: datetime | None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(MARKET_TZ)


def trading_date(now: datetime | None = None) -> str:
    return _eastern(now).date().isoformat()


def session_fraction(now: datetime | None = None) -> float | None:
    """Fraction of the regular session elapsed, in (0, 1); None when closed.

    shortcut: holidays are unknown, so a weekday holiday reads as open; add a calendar if it matters.
    """
    et = _eastern(now)
    if et.weekday() >= 5:
        return None
    elapsed = (et.hour * 60 + et.minute + et.second / 60) - (SESSION_OPEN.hour * 60 + SESSION_OPEN.minute)
    if elapsed <= 0 or elapsed >= SESSION_MINUTES:
        return None
    return elapsed / SESSION_MINUTES
