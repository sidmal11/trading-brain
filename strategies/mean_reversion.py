"""Mean reversion (rule sheet 6, approved 2026-11-18): a sharp dip below the lower Bollinger band inside a
long-term uptrend, entered on the first up day; the target is the 20-day average.
"""
from indicators import adx, atr, bollinger, rsi, sma
from strategies.common import finish, item, not_enough

VERSION = "1"
NAME = "mean_reversion"
SIGNAL_TYPE = "MEAN_REVERSION"
MAX_HOLD = 10
ITEMS = [
    ("uptrend", "Close above the 200-day average", 25),
    ("band", "Close below the lower Bollinger band (20, 2) on one of the last 2 days", 25),
    ("rsi", "RSI(14) below 30 on one of the last 3 days", 20),
    ("calm", "Not in a strong trend: ADX(14) below 25", 15),
    ("upday", "First up day: close above the previous close", 15),
]
BUY_KEYS = ("uptrend", "band", "upday")
WARMUP = 220


def evaluate_series(bars, spy=None):
    n = len(bars)
    high, low, close = [b.high for b in bars], [b.low for b in bars], [b.close for b in bars]
    s200, bands, r = sma(close, 200), bollinger(close, 20, 2.0), rsi(close, 14)
    d, a = adx(high, low, close, 14)["adx"], atr(high, low, close, 14)
    out = []
    for t in range(n):
        if t < WARMUP or s200[t] is None or d[t] is None:
            out.append(not_enough(NAME, VERSION, SIGNAL_TYPE, bars[t].date, ITEMS))
            continue
        pb = [bands["percent_b"][i] for i in (t - 1, t)]
        recent_rsi = [r[i] for i in range(t - 2, t + 1) if r[i] is not None]
        items = [
            item("uptrend", ITEMS[0][1], close[t] > s200[t], 25, f"Close {close[t]:.2f} vs 200-day {s200[t]:.2f}"),
            item("band", ITEMS[1][1], any(p is not None and p < 0 for p in pb), 25,
                 "%B " + ", ".join("-" if p is None else f"{p:.2f}" for p in pb) + " (below 0 is under the band)"),
            item("rsi", ITEMS[2][1], bool(recent_rsi) and min(recent_rsi) < 30, 20,
                 f"Lowest RSI in 3 days {min(recent_rsi):.1f}" if recent_rsi else "No reading"),
            item("calm", ITEMS[3][1], d[t] < 25, 15, f"ADX {d[t]:.1f}"),
            item("upday", ITEMS[4][1], close[t] > close[t - 1], 15, f"Close {close[t]:.2f} vs {close[t - 1]:.2f}"),
        ]
        entry = close[t]
        out.append(finish(NAME, VERSION, SIGNAL_TYPE, bars[t].date, items, BUY_KEYS, entry, entry - 1.5 * a[t],
                          bands["middle"][t]))  # no trade when the 20-day average is not above the entry
    return out
