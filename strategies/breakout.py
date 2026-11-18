"""Breakout (rule sheet 3, approved 2026-11-18): a closing break of a 20-day high on strong volume after a
quiet spell, without chasing. Intraday moves never count: only completed daily closes.
"""
from indicators import atr, bollinger, relative_volume, rolling_percentile
from strategies.common import finish, item, not_enough, spy_trend_by_date

VERSION = "1"
NAME = "breakout"
SIGNAL_TYPE = "BREAKOUT"
MAX_HOLD = 20
LOOKBACK = 20
ITEMS = [
    ("break", "Close above the highest high of the previous 20 days", 30),
    ("volume", "Relative volume at least 1.5 on that day", 25),
    ("squeeze", "Bollinger bandwidth at or below its 30th percentile on one of the 5 days before", 15),
    ("extended", "Not extended: close at most 3% above the broken level", 15),
    ("regime", "Market regime trend not bear", 15),
]
BUY_KEYS = ("break", "volume", "extended")
WARMUP = 280  # 252-bar bandwidth percentile plus the band's own 20


def evaluate_series(bars, spy=None):
    n = len(bars)
    high, low, close = [b.high for b in bars], [b.low for b in bars], [b.close for b in bars]
    a = atr(high, low, close, 14)
    width = rolling_percentile(bollinger(close, 20, 2.0)["bandwidth"], 252)
    volumes = [b.volume for b in bars]
    rvol = relative_volume(volumes, LOOKBACK) if None not in volumes else [None] * n
    market = spy_trend_by_date(spy)
    out, last_break = [], None  # (index, level) of the latest closing break
    for t in range(n):
        if t < WARMUP or a[t] is None:
            out.append(not_enough(NAME, VERSION, SIGNAL_TYPE, bars[t].date, ITEMS))
            continue
        level = max(high[t - LOOKBACK:t])
        broke = close[t] > level
        recent = [w for w in width[t - 5:t] if w is not None]
        regime = market.get(bars[t].date)
        items = [
            item("break", ITEMS[0][1], broke, 30, f"Close {close[t]:.2f} vs 20-day high {level:.2f}"),
            item("volume", ITEMS[1][1], rvol[t] is not None and rvol[t] >= 1.5, 25,
                 "No volume reading" if rvol[t] is None else f"Relative volume {rvol[t]:.2f}"),
            item("squeeze", ITEMS[2][1], bool(recent) and min(recent) <= 30, 15,
                 f"Lowest bandwidth percentile in the 5 days before: {min(recent):.0f}" if recent else "No reading"),
            item("extended", ITEMS[3][1], close[t] <= 1.03 * level, 15,
                 f"Close {(close[t] / level - 1) * 100:.2f}% from the level"),
            item("regime", ITEMS[4][1], regime is not None and regime != "bear", 15,
                 f"Market trend: {regime}" if regime else "No market reading for this day"),
        ]
        entry, stop = close[t], level - 0.5 * a[t]
        result = finish(NAME, VERSION, SIGNAL_TYPE, bars[t].date, items, BUY_KEYS, entry, stop,
                        entry + 2 * (entry - stop))
        if result["direction"] == "bullish":
            state = "confirmed"
        elif last_break and t - last_break[0] <= 3 and close[t] < last_break[1]:
            state = "failed"
        elif last_break and t - last_break[0] <= 5 and low[t] <= 1.01 * last_break[1] and close[t] > last_break[1]:
            state = "retest"
        elif not broke and close[t] >= 0.99 * level:
            state = "potential"
        else:
            state = "none"
        if broke:
            last_break = (t, level)
        result["state"] = state
        out.append(result)
    return out
