"""Weekly bars resampled from completed daily bars (R7). Only completed weeks: the newest week counts once no
trading day of it remains after the last daily bar (NYSE calendar)."""
from datetime import date, timedelta

from market_data.bars import Bar
from market_data.calendar import is_trading_day


def _week_done(last_day: date) -> bool:
    """True when no trading day of `last_day`'s ISO week comes after it."""
    d = last_day + timedelta(days=1)
    while d.isocalendar()[:2] == last_day.isocalendar()[:2]:
        if is_trading_day(d):
            return False
        d += timedelta(days=1)
    return True


def weekly_bars(bars) -> list:
    """One Bar per completed ISO week, dated by its last trading day, oldest first."""
    groups = []
    for b in bars:
        key = date.fromisoformat(b.date).isocalendar()[:2]
        if groups and groups[-1][0] == key:
            groups[-1][1].append(b)
        else:
            groups.append((key, [b]))
    if groups and not _week_done(date.fromisoformat(groups[-1][1][-1].date)):
        groups.pop()
    out = []
    for _, days in groups:
        volumes = [d.volume for d in days]
        out.append(Bar(days[-1].date, max(d.high for d in days), min(d.low for d in days), days[-1].close,
                       days[0].open, None if None in volumes else sum(volumes), days[-1].adj_close))
    return out
