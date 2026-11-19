"""Momentum (rule sheet 2, approved 2026-11-18): positive, accelerating momentum that beats the market
without being stretched. Long only.
"""
from features.relative_strength import _price
from indicators import atr, macd, rsi, stoch_rsi
from strategies.common import finish, item, not_enough

VERSION = "1"
NAME = "momentum"
SIGNAL_TYPE = "MOMENTUM"
MAX_HOLD = 20
ITEMS = [
    ("rsi", "RSI(14) between 55 and 70", 20),
    ("macd", "MACD histogram above 0 and higher than the day before", 25),
    ("relative", "20-day return above 0 and above SPY's", 25),
    ("stoch", "Stochastic RSI above 50 and higher than the day before", 15),
    ("rising", "RSI rising over the last 5 days", 15),
]
BUY_KEYS = ("macd", "relative")  # plus at least one other check
WARMUP = 60


def evaluate_series(bars, spy=None):
    n = len(bars)
    high, low, close = [b.high for b in bars], [b.low for b in bars], [b.close for b in bars]
    r, st, hist, a = rsi(close, 14), stoch_rsi(close, 14), macd(close)[2], atr(high, low, close, 14)
    bench = {b.date: _price(b) for b in spy or []}  # only bars[t] and bars[t - 20] dates are read
    out = []
    for t in range(n):
        if t < WARMUP or r[t - 5] is None or a[t] is None:
            out.append(not_enough(NAME, VERSION, SIGNAL_TYPE, bars[t].date, ITEMS))
            continue
        own = (_price(bars[t]) / _price(bars[t - 20]) - 1) * 100
        b0, b1 = bench.get(bars[t - 20].date), bench.get(bars[t].date)
        market = (b1 / b0 - 1) * 100 if b0 and b1 else None
        s0, s1 = st[t - 1], st[t]
        items = [
            item("rsi", ITEMS[0][1], 55 <= r[t] <= 70, 20, f"RSI {r[t]:.1f}"),
            item("macd", ITEMS[1][1], hist[t] > 0 and hist[t] > hist[t - 1], 25,
                 f"Histogram {hist[t]:+.3f} vs {hist[t - 1]:+.3f} the day before"),
            item("relative", ITEMS[2][1], market is not None and own > 0 and own > market, 25,
                 f"20-day return {own:+.2f}%" + (f" vs SPY {market:+.2f}%" if market is not None else ", no SPY reading")),
            item("stoch", ITEMS[3][1], None not in (s0, s1) and s1 > 50 and s1 > s0, 15,
                 "No reading" if None in (s0, s1) else f"Stochastic RSI {s1:.0f} vs {s0:.0f}"),
            item("rising", ITEMS[4][1], r[t] > r[t - 5], 15, f"RSI {r[t]:.1f} vs {r[t - 5]:.1f} 5 days ago"),
        ]
        entry, stop = close[t], close[t] - 1.5 * a[t]
        result = finish(NAME, VERSION, SIGNAL_TYPE, bars[t].date, items, BUY_KEYS, entry, stop,
                        entry + 2 * (entry - stop))
        if not any(x["passed"] for x in items if x["key"] not in BUY_KEYS):
            result["direction"] = "neutral"
        out.append(result)
    return out
