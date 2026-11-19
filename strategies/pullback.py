"""Price action pullback (rule sheet 4, approved 2026-11-18): a pullback to support inside an uptrend that ends
with a bullish candle. A candle alone never makes a BUY. Long only.
"""
from features.price_action import SWING_K, candlesticks, levels, structure, swings
from indicators import atr, ema
from strategies.common import finish, item, not_enough

VERSION = "1"
NAME = "pullback"
SIGNAL_TYPE = "PULLBACK"
MAX_HOLD = 20
ITEMS = [
    ("structure", "Swing structure up", 25),
    ("support", "In the last 3 days the low came within 1 ATR of the nearest support or the 20-day EMA", 25),
    ("candle", "Bullish candle on the last day: bullish engulfing, hammer or morning star", 20),
    ("intact", "Close above the most recent swing low (structure not broken)", 15),
    ("room", "Nearest resistance at least 2 x the risk away, or none above", 15),
]
BUY_KEYS = ("structure", "support", "candle")
BULLISH = {"bullish_engulfing", "hammer", "morning_star"}
WARMUP = 60


def evaluate_series(bars, spy=None):
    """One result per bar; bar t reads bars[0..t] only (swings need SWING_K later bars)."""
    n = len(bars)
    high, low, close = [b.high for b in bars], [b.low for b in bars], [b.close for b in bars]
    e20, a = ema(close, 20), atr(high, low, close, 14)
    sw_high, sw_low = swings(bars)
    out, hi_i, lo_i = [], 0, 0
    for t in range(n):
        while hi_i < len(sw_high) and sw_high[hi_i][0] <= t - SWING_K:
            hi_i += 1
        while lo_i < len(sw_low) and sw_low[lo_i][0] <= t - SWING_K:
            lo_i += 1
        if t < WARMUP or a[t] is None:
            out.append(not_enough(NAME, VERSION, SIGNAL_TYPE, bars[t].date, ITEMS))
            continue
        highs, lows = sw_high[:hi_i], sw_low[:lo_i]
        found = levels(bars[:t + 1], highs, lows, tolerance=0.5 * a[t])
        supports = [lv["price"] for lv in found if lv["kind"] == "support"]
        resistances = [lv["price"] for lv in found if lv["kind"] == "resistance"]
        support = max(supports) if supports else None
        touched = [i for i in range(t - 2, t + 1)
                   if abs(low[i] - e20[i]) <= a[t] or (support is not None and abs(low[i] - support) <= a[t])]
        candles = BULLISH & set(candlesticks(bars[t - 2:t + 1]))
        entry, stop = close[t], min(low[t - 2:t + 1]) - 0.25 * a[t]  # the pullback low minus a quarter ATR
        resistance = min(resistances) if resistances else None
        room = resistance is None or resistance - entry >= 2 * (entry - stop)
        shape = structure(highs, lows)
        items = [
            item("structure", ITEMS[0][1], shape == "up", 25, f"Structure: {shape or 'too few swings'}"),
            item("support", ITEMS[1][1], bool(touched), 25,
                 f"20-day EMA {e20[t]:.2f}, nearest support " + (f"{support:.2f}" if support else "none")),
            item("candle", ITEMS[2][1], bool(candles), 20, ", ".join(sorted(candles)) or "No bullish candle"),
            item("intact", ITEMS[3][1], bool(lows) and close[t] > lows[-1][1], 15,
                 f"Close {close[t]:.2f} vs swing low {lows[-1][1]:.2f}" if lows else "No swing low yet"),
            item("room", ITEMS[4][1], room, 15,
                 f"Resistance {resistance:.2f}, {resistance - entry:.2f} away" if resistance else "No resistance above"),
        ]
        out.append(finish(NAME, VERSION, SIGNAL_TYPE, bars[t].date, items, BUY_KEYS, entry, stop,
                          entry + 2 * (entry - stop)))
    return out
