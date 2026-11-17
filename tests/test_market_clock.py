import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.market_clock import MIN_SESSION_FRACTION, session_fraction, trading_date


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_midday_winter_fraction():
    # Mon 2026-11-16 12:45 ET = 17:45 UTC, 195 of 390 minutes
    assert session_fraction(utc(2026, 11, 16, 17, 45)) == 0.5


def test_summer_open_uses_daylight_time():
    # Wed 2026-07-15 10:30 ET = 14:30 UTC, 60 minutes in
    assert round(session_fraction(utc(2026, 7, 15, 14, 30)), 4) == round(60 / 390, 4)


def test_closed_before_open_at_close_and_weekend():
    assert session_fraction(utc(2026, 11, 16, 14, 29)) is None   # 09:29 ET
    assert session_fraction(utc(2026, 11, 16, 21, 0)) is None    # 16:00 ET
    assert session_fraction(utc(2026, 11, 14, 17, 0)) is None    # Saturday


def test_at_open_is_none_because_nothing_has_elapsed():
    assert session_fraction(utc(2026, 11, 16, 14, 30)) is None


def test_trading_date_is_eastern_not_utc():
    assert trading_date(utc(2026, 11, 17, 2, 0)) == "2026-11-16"  # 21:00 ET Monday


def test_min_fraction_is_thirty_minutes():
    assert MIN_SESSION_FRACTION == 30 / 390
