"""US equity regular-session clock: 09:30 to 16:00 America/New_York (13:00 on half days), NYSE trading days."""
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from market_data.calendar import session_minutes

MARKET_TZ = ZoneInfo("America/New_York")
SESSION_OPEN = time(9, 30)
SESSION_MINUTES = 390
MIN_SESSION_FRACTION = 30 / SESSION_MINUTES


def _eastern(now: datetime | None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(MARKET_TZ)


def trading_date(now: datetime | None = None) -> str:
    return _eastern(now).date().isoformat()


def session_fraction(now: datetime | None = None) -> float | None:
    """Fraction of the regular session elapsed, in (0, 1); None when closed (weekend, holiday, outside hours)."""
    et = _eastern(now)
    minutes = session_minutes(et.date())
    if minutes is None:
        return None
    elapsed = (et.hour * 60 + et.minute + et.second / 60) - (SESSION_OPEN.hour * 60 + SESSION_OPEN.minute)
    if elapsed <= 0 or elapsed >= minutes:
        return None
    return elapsed / minutes
