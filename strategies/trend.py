"""Trend following (rule sheet 1, approved 2026-11-18): an established uptrend with strength behind it."""
from features.price_action import SWING_K, swings
from indicators import adx, atr, ema, sma
from strategies.common import finish, item, not_enough

VERSION = "1"
NAME = "trend"
SIGNAL_TYPE = "TREND_FOLLOWING"
MAX_HOLD = 20
ITEMS = [
    ("ma", "Close above the 50-day average, and the 50-day above the 200-day", 25),
    ("slope", "50-day average rising over the last 20 days", 15),
    ("ema", "20-day EMA above the 50-day EMA", 15),
    ("adx", "ADX(14) at least 25, with +DI above -DI", 25),
    ("structure", "Swing structure up (a higher high and a higher low)", 20),
]
BUY_KEYS = ("ma", "adx", "structure")
WARMUP = 220


def _label(score):
    return "strong bullish" if score >= 80 else "bullish" if score >= 60 else "weak bullish" if score >= 40 else "neutral"


def evaluate_series(bars, spy=None):
    """One result per bar; bar t reads bars[0..t] only (swings need SWING_K later bars)."""
    n = len(bars)
    high, low, close = [b.high for b in bars], [b.low for b in bars], [b.close for b in bars]
    s50, s200, e20, e50 = sma(close, 50), sma(close, 200), ema(close, 20), ema(close, 50)
    d, a = adx(high, low, close, 14), atr(high, low, close, 14)
    sw_high, sw_low = swings(bars)
    out = []
    hi_i = lo_i = 0
    for t in range(n):
        while hi_i < len(sw_high) and sw_high[hi_i][0] <= t - SWING_K:
            hi_i += 1
        while lo_i < len(sw_low) and sw_low[lo_i][0] <= t - SWING_K:
            lo_i += 1
        if t < WARMUP or s200[t] is None or d["adx"][t] is None:
            out.append(not_enough(NAME, VERSION, SIGNAL_TYPE, bars[t].date, ITEMS))
            continue
        highs, lows = sw_high[:hi_i], sw_low[:lo_i]
        up = len(highs) >= 2 and len(lows) >= 2 and highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]
        plus, minus = d["plus_di"][t], d["minus_di"][t]
        items = [
            item("ma", ITEMS[0][1], close[t] > s50[t] > s200[t], 25,
                 f"Close {close[t]:.2f}, 50-day {s50[t]:.2f}, 200-day {s200[t]:.2f}"),
            item("slope", ITEMS[1][1], s50[t] > s50[t - 20], 15, f"50-day {s50[t]:.2f} vs {s50[t - 20]:.2f} 20 days ago"),
            item("ema", ITEMS[2][1], e20[t] > e50[t], 15, f"EMA20 {e20[t]:.2f}, EMA50 {e50[t]:.2f}"),
            item("adx", ITEMS[3][1], d["adx"][t] >= 25 and plus > minus, 25,
                 f"ADX {d['adx'][t]:.1f}, +DI {plus:.1f}, -DI {minus:.1f}"),
            item("structure", ITEMS[4][1], up, 20,
                 "Higher high and higher low" if up else "No higher high and higher low in the last swings"),
        ]
        entry = close[t]
        swing_low = lows[-1][1] if lows else None
        # The last swing low, unless it is missing, above the price or within 1 ATR: then 2 ATR below entry.
        stop = swing_low if swing_low is not None and entry - swing_low >= a[t] else entry - 2 * a[t]
        result = finish(NAME, VERSION, SIGNAL_TYPE, bars[t].date, items, BUY_KEYS, entry, stop,
                        entry + 2 * (entry - stop))
        result["label"] = _label(result["confidence"])
        out.append(result)
    return out
