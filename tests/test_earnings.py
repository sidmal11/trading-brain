"""Next earnings dates: picked from Yahoo's list, counted in trading days, stored, warned about."""
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_data import earnings  # noqa: E402
from market_data.earnings import EarningsStore, earnings_note, next_earnings, trading_days_until  # noqa: E402

TODAY = date(2026, 11, 18)  # Wednesday


def test_next_date_is_the_earliest_on_or_after_today():
    assert next_earnings([date(2026, 11, 20), date(2026, 11, 19)], TODAY) == date(2026, 11, 19)
    assert next_earnings([datetime(2026, 11, 18, 21, 0)], TODAY) == date(2026, 11, 18)  # datetimes too


def test_only_past_dates_or_none_means_unknown():
    assert next_earnings([date(2026, 11, 17)], TODAY) is None  # Yahoo had not rolled NVDA forward
    assert next_earnings(None, TODAY) is None and next_earnings([], TODAY) is None


def test_trading_days_skip_weekends_and_holidays():
    assert trading_days_until(date(2026, 11, 18), TODAY) == 0
    assert trading_days_until(date(2026, 11, 23), TODAY) == 3  # Thu, Fri, Mon
    assert trading_days_until(date(2026, 11, 30), date(2026, 11, 25)) == 2  # Thanksgiving skipped: Fri 27, Mon 30


def test_note_within_three_trading_days_only():
    assert earnings_note(date(2026, 11, 18), TODAY) == "Earnings today (Nov 18)"
    assert earnings_note(date(2026, 11, 19), TODAY) == "Earnings in 1 day (Nov 19)"
    assert earnings_note(date(2026, 11, 23), TODAY) == "Earnings in 3 days (Nov 23)"
    assert earnings_note(date(2026, 11, 24), TODAY) is None
    assert earnings_note(None, TODAY) is None and earnings_note(date(2026, 11, 1), TODAY) is None


def test_store_round_trip_and_unknown(tmp_path):
    store = EarningsStore(str(tmp_path / "s.db"))
    store.save({"WMT": date(2026, 11, 19), "SPY": None})
    store.save({"WMT": date(2026, 11, 20)})
    assert store.get(["WMT", "SPY", "AAPL"]) == {"WMT": date(2026, 11, 20), "SPY": None}


def test_download_skips_failures_and_keeps_unknowns(monkeypatch):
    class T:
        def __init__(self, symbol):
            self.symbol = symbol

        @property
        def calendar(self):
            if self.symbol == "BAD":
                raise RuntimeError("Yahoo down")
            return {"Earnings Date": [date(2026, 11, 19)]} if self.symbol == "WMT" else {}

    monkeypatch.setattr(earnings.yf, "Ticker", T)
    assert earnings.download_earnings(["WMT", "SPY", "BAD"], TODAY) == {"WMT": date(2026, 11, 19), "SPY": None}
