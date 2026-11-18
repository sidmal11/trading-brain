"""NYSE calendar: holidays, half days, previous trading day; session_fraction uses it."""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_data import calendar  # noqa: E402
from utils.market_clock import session_fraction  # noqa: E402


def test_holiday_and_weekend_are_closed():
    assert calendar.is_trading_day(date(2026, 11, 26)) is False  # Thanksgiving
    assert calendar.session_minutes(date(2026, 11, 28)) is None  # Saturday
    assert calendar.is_trading_day(date(2026, 11, 25)) is True


def test_half_day_is_210_minutes():
    assert calendar.session_minutes(date(2026, 11, 27)) == 210
    assert calendar.session_minutes(date(2026, 11, 25)) == 390


def test_previous_trading_day_skips_weekends_and_holidays():
    assert calendar.previous_trading_day(date(2026, 11, 30)) == date(2026, 11, 27)
    assert calendar.previous_trading_day(date(2026, 11, 27)) == date(2026, 11, 25)
    assert calendar.previous_trading_day(date(2027, 1, 4)) == date(2026, 12, 31)


def test_table_covers_the_next_60_days():
    # Fails when the holiday table runs out: add the next year's dates from nyse.com.
    assert calendar.COVERED_UNTIL >= date.today() + timedelta(days=60)


def test_session_fraction_on_a_holiday_and_a_half_day():
    assert session_fraction(datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc)) is None
    assert session_fraction(datetime(2026, 11, 27, 17, 0, tzinfo=timezone.utc)) == 150 / 210  # 12:00 ET
    assert session_fraction(datetime(2026, 11, 27, 18, 31, tzinfo=timezone.utc)) is None  # after the 13:00 close
