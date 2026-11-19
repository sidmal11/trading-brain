"""Volatility squeeze (rule sheet 7, approved 2026-11-18): a quiet spell that ends with a wide up day. Long only."""
from indicators import atr, atr_pct, bollinger, rolling_percentile, sma, true_range
from strategies.common import finish, item, not_enough

VERSION = "1"
NAME = "squeeze"
SIGNAL_TYPE = "VOLATILITY_SQUEEZE"
MAX_HOLD = 20
ITEMS = [
    ("squeeze", "Bollinger bandwidth at or below its 20th percentile on one of the last 10 days", 30),
    ("wide", "Today's true range at least 1.5 x ATR(14)", 25),
    ("close", "Close in the top quarter of today's range and above the open", 20),
    ("calm", "ATR % below its 80th percentile (not already wild)", 10),
    ("ma", "Close above the 50-day average", 15),
]
BUY_KEYS = ("squeeze", "wide", "close")
WARMUP = 280  # 252-bar percentiles plus the indicators' own warm-up


def evaluate_series(bars, spy=None):
    n = len(bars)
    high, low, close = [b.high for b in bars], [b.low for b in bars], [b.close for b in bars]
    a, tr, s50 = atr(high, low, close, 14), true_range(high, low, close), sma(close, 50)
    width = rolling_percentile(bollinger(close, 20, 2.0)["bandwidth"], 252)
    calm = rolling_percentile(atr_pct(high, low, close, 14), 252)
    out = []
    for t in range(n):
        recent = [w for w in width[max(0, t - 9):t + 1] if w is not None]
        if t < WARMUP or a[t] is None or not recent:
            out.append(not_enough(NAME, VERSION, SIGNAL_TYPE, bars[t].date, ITEMS))
            continue
        b, rng = bars[t], high[t] - low[t]
        top = rng > 0 and b.close >= b.high - 0.25 * rng and b.close > b.open
        items = [
            item("squeeze", ITEMS[0][1], min(recent) <= 20, 30, f"Lowest bandwidth percentile in 10 days: {min(recent):.0f}"),
            item("wide", ITEMS[1][1], tr[t] >= 1.5 * a[t], 25, f"True range {tr[t]:.2f} = {tr[t] / a[t]:.2f} x ATR"),
            item("close", ITEMS[2][1], top, 20, f"Close {b.close:.2f}, range {low[t]:.2f}-{high[t]:.2f}, open {b.open:.2f}"),
            item("calm", ITEMS[3][1], calm[t] is not None and calm[t] < 80, 10,
                 "No reading" if calm[t] is None else f"ATR % percentile {calm[t]:.0f}"),
            item("ma", ITEMS[4][1], close[t] > s50[t], 15, f"Close {close[t]:.2f} vs 50-day {s50[t]:.2f}"),
        ]
        entry, stop = close[t], low[t]  # stop: today's low
        out.append(finish(NAME, VERSION, SIGNAL_TYPE, bars[t].date, items, BUY_KEYS, entry, stop,
                          entry + 2 * (entry - stop)))
    return out
