"""Daily bar checks: invalid stops analysis, warn is shown but analysed."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_quality.checks import check_bars  # noqa: E402
from market_data.bars import Bar  # noqa: E402

TODAY = date(2026, 11, 18)  # Wednesday; previous trading day 2026-11-17
DATES = ["2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17"]


def bars(dates=DATES, **change):
    out = [Bar(d, 11.0, 9.0, 10.0, 10.0, 1000, 10.0) for d in dates]
    for i, fields in change.items():
        out[int(i[1:])] = out[int(i[1:])]._replace(**fields)
    return out


def test_clean_bars_are_valid():
    assert check_bars(bars(), TODAY, set(DATES)) == {"status": "valid", "issues": []}


def test_no_bars_is_invalid():
    assert check_bars([], TODAY, None)["status"] == "invalid"


def test_duplicate_date_is_invalid():
    r = check_bars(bars(DATES + ["2026-11-17"]), TODAY, None)
    assert r["status"] == "invalid" and any("Duplicate" in i for i in r["issues"])


def test_non_positive_or_missing_price_is_invalid():
    assert check_bars(bars(b1={"low": 0.0}), TODAY, None)["status"] == "invalid"
    assert check_bars(bars(b1={"close": None}), TODAY, None)["status"] == "invalid"


def test_high_low_that_do_not_contain_open_and_close_are_invalid():
    assert check_bars(bars(b2={"high": 9.5}), TODAY, None)["status"] == "invalid"
    assert check_bars(bars(b2={"low": 10.5}), TODAY, None)["status"] == "invalid"


def test_stale_newest_bar_is_invalid():
    r = check_bars(bars(DATES[:-1]), TODAY, None)
    assert r["status"] == "invalid" and any("Stale" in i for i in r["issues"])


def test_missing_session_against_spy_is_a_warning():
    r = check_bars(bars([d for d in DATES if d != "2026-11-13"]), TODAY, set(DATES))
    assert r["status"] == "warn" and any("2026-11-13" in i for i in r["issues"])


def test_holiday_is_not_a_gap():
    # SPY has no bar on a holiday either, so a missing holiday is never flagged.
    assert check_bars(bars(), TODAY, set(DATES))["status"] == "valid"


def test_zero_and_extreme_volume_are_warnings():
    assert check_bars(bars(b1={"volume": 0}), TODAY, None)["status"] == "warn"
    many = [f"2026-{m:02d}-{d:02d}" for m in range(1, 11) for d in range(1, 29) if date(2026, m, d).weekday() < 5]
    series = bars(many[-55:] + ["2026-11-17"])
    series[-1] = series[-1]._replace(volume=21_000)
    r = check_bars(series, TODAY, None)
    assert r["status"] == "warn" and any("Volume" in i for i in r["issues"])


def _long(n):
    out, day = [], date(2024, 1, 1)
    from datetime import timedelta
    while len(out) < n:
        if day.weekday() < 5:
            out.append(Bar(day.isoformat(), 11.0, 9.0, 10.0, 10.0, 1000, 10.0))
        day += timedelta(days=1)
    return out[:-1] + [out[-1]._replace(date="2026-11-17")]


def test_a_broken_bar_outside_the_decision_window_is_only_a_warning():
    series = _long(600)
    series[10] = series[10]._replace(high=9.5)  # 590 bars back: no strategy reads it
    r = check_bars(series, TODAY, None)
    assert r["status"] == "warn" and any("High and low" in i for i in r["issues"])
    series[-5] = series[-5]._replace(high=9.5)  # inside the last 500 bars
    assert check_bars(series, TODAY, None)["status"] == "invalid"
