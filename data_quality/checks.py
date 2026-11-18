"""Checks on stored daily bars. "invalid" means the ticker is not analysed; "warn" is shown, still analysed.

Nothing is repaired here: a bad bar is reported, never filled in or dropped.
"""
from datetime import date
from statistics import median

from market_data.calendar import previous_trading_day

EXTREME_VOLUME = 20  # times the median of the previous 50 bars
_SHOW = 3  # dates listed per issue


def _dates(ds):
    return ", ".join(ds[:_SHOW]) + (f" and {len(ds) - _SHOW} more" if len(ds) > _SHOW else "")


def check_bars(bars, today: date, reference_dates: set | None) -> dict:
    """{"status": "valid" | "warn" | "invalid", "issues": [str]} for one ticker's bars (oldest first).

    `reference_dates` are SPY's stored dates: a session SPY has and this ticker lacks is a gap.
    """
    if not bars:
        return {"status": "invalid", "issues": ["No bars stored."]}
    invalid, warn = [], []
    dates = [b.date for b in bars]
    if dupes := sorted({d for d in dates if dates.count(d) > 1}):
        invalid.append(f"Duplicate dates: {_dates(dupes)}")
    missing = [b.date for b in bars if None in (b.open, b.high, b.low, b.close)]
    if missing:
        invalid.append(f"Missing prices on {_dates(missing)}")
    priced = [b for b in bars if None not in (b.open, b.high, b.low, b.close)]
    if bad := [b.date for b in priced if min(b.open, b.high, b.low, b.close) <= 0]:
        invalid.append(f"Price at or below zero on {_dates(bad)}")
    if bad := [b.date for b in priced if b.low > min(b.open, b.close) or b.high < max(b.open, b.close)]:
        invalid.append(f"High and low do not contain open and close on {_dates(bad)}")
    due = previous_trading_day(today).isoformat()
    if dates[-1] < due:
        invalid.append(f"Stale: newest bar {dates[-1]}, expected {due}")

    if reference_dates:
        have = set(dates)
        if gaps := sorted(d for d in reference_dates if dates[0] < d <= dates[-1] and d not in have):
            warn.append(f"{len(gaps)} missing session{'s' if len(gaps) != 1 else ''} vs SPY: {_dates(gaps)}")
    if zero := [b.date for b in bars if b.volume == 0]:
        warn.append(f"Volume 0 on {_dates(zero)}")
    spikes = [bars[i].date for i in range(50, len(bars))
              if bars[i].volume and (m := median(b.volume or 0 for b in bars[i - 50:i])) > 0
              and bars[i].volume > EXTREME_VOLUME * m]
    if spikes:
        warn.append(f"Volume over {EXTREME_VOLUME}x the 50-day median on {_dates(spikes)}")

    status = "invalid" if invalid else "warn" if warn else "valid"
    return {"status": status, "issues": invalid + warn}
