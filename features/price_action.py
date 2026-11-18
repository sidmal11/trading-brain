"""Price action on completed daily bars: swings, market structure, support and resistance, gaps, breakouts,
candlesticks. Point in time: a swing needs SWING_K bars after it, so the newest SWING_K bars never hold one.
"""
from indicators import atr

SWING_K = 2
LEVEL_LOOKBACK = 250
BREAKOUT_BARS = 20


def swings(bars, k=SWING_K):
    """([(index, high)], [(index, low)]) of confirmed swing highs and lows.

    A swing high at i: its high beats the k bars before it and is not beaten by the k bars after it.
    """
    highs, lows = [], []
    for i in range(k, len(bars) - k):
        before, after = bars[i - k:i], bars[i + 1:i + k + 1]
        if bars[i].high > max(b.high for b in before) and bars[i].high >= max(b.high for b in after):
            highs.append((i, bars[i].high))
        if bars[i].low < min(b.low for b in before) and bars[i].low <= min(b.low for b in after):
            lows.append((i, bars[i].low))
    return highs, lows


def structure(highs, lows):
    """"up" (higher high and higher low), "down" (lower high and lower low), "mixed", or None (too few swings)."""
    if len(highs) < 2 or len(lows) < 2:
        return None
    hh, hl = highs[-1][1] > highs[-2][1], lows[-1][1] > lows[-2][1]
    return "up" if hh and hl else "down" if not hh and not hl else "mixed"


def levels(bars, highs, lows, tolerance):
    """Support and resistance candidates: swing points of the last LEVEL_LOOKBACK bars grouped within `tolerance`."""
    start = len(bars) - LEVEL_LOOKBACK
    points = sorted((p, i) for i, p in highs + lows if i >= start)
    groups = []
    for price, i in points:
        if groups and price - groups[-1][-1][0] <= tolerance:
            groups[-1].append((price, i))
        else:
            groups.append([(price, i)])
    close = bars[-1].close
    out = []
    for g in groups:
        price = sum(p for p, _ in g) / len(g)
        last = max(i for _, i in g)
        out.append({"price": price, "reactions": len(g), "strength": len(g), "lastTouch": bars[last].date,
                    "distancePct": (price / close - 1) * 100, "kind": "support" if price < close else "resistance"})
    return out


def _parts(b):
    body = abs(b.close - b.open)
    return body, b.high - b.low, b.high - max(b.open, b.close), min(b.open, b.close) - b.low


def candlesticks(bars):
    """Pattern names found on the last bar (with the bars before it where a pattern needs them)."""
    if not bars:
        return []
    found = []
    b = bars[-1]
    body, rng, upper, lower = _parts(b)
    if rng > 0 and body <= 0.1 * rng:
        found.append("doji")
    if body > 0 and lower >= 2 * body and upper <= body:
        found.append("hammer")
    if body > 0 and upper >= 2 * body and lower <= body:
        found.append("shooting_star")
    if len(bars) >= 2:
        p = bars[-2]
        p_body = abs(p.close - p.open)
        if p.close < p.open and b.close > b.open and b.open <= p.close and b.close >= p.open and body > p_body:
            found.append("bullish_engulfing")
        if p.close > p.open and b.close < b.open and b.open >= p.close and b.close <= p.open and body > p_body:
            found.append("bearish_engulfing")
        if b.high < p.high and b.low > p.low:
            found.append("inside_bar")
        if b.high > p.high and b.low < p.low:
            found.append("outside_bar")
    if len(bars) >= 3:
        a, m = bars[-3], bars[-2]
        a_body, a_rng = abs(a.close - a.open), a.high - a.low
        m_body = abs(m.close - m.open)
        big = a_rng > 0 and a_body >= 0.6 * a_rng
        small = m_body <= 0.3 * a_body
        mid = (a.open + a.close) / 2
        if big and small and a.close < a.open and b.close > b.open and b.close > mid:
            found.append("morning_star")
        if big and small and a.close > a.open and b.close < b.open and b.close < mid:
            found.append("evening_star")
    return found


def price_action(bars) -> dict:
    """All price-action features for the last bar of `bars` (completed bars, oldest first)."""
    out = {"structure": None, "lastSwingHigh": None, "lastSwingLow": None, "breakOfStructure": None,
           "nearestSupport": None, "nearestResistance": None, "gapPct": None, "rangeExpansion": None,
           "breakout": None, "candlesticks": [], "nearLevel": None}
    if len(bars) < 2:
        return out
    highs, lows = swings(bars)
    out["structure"] = structure(highs, lows)
    close = bars[-1].close
    if highs:
        out["lastSwingHigh"] = {"price": highs[-1][1], "date": bars[highs[-1][0]].date}
    if lows:
        out["lastSwingLow"] = {"price": lows[-1][1], "date": bars[lows[-1][0]].date}
    out["breakOfStructure"] = ("bullish" if highs and close > highs[-1][1] else
                               "bearish" if lows and close < lows[-1][1] else "none")
    prev = bars[-2]
    out["gapPct"] = (bars[-1].open / prev.close - 1) * 100 if prev.close else None
    a = atr([x.high for x in bars], [x.low for x in bars], [x.close for x in bars], 14)[-1]
    if a:
        tr = max(bars[-1].high - bars[-1].low, abs(bars[-1].high - prev.close), abs(bars[-1].low - prev.close))
        out["rangeExpansion"] = tr / a
        found = levels(bars, highs, lows, tolerance=0.5 * a)
        supports = [lv for lv in found if lv["kind"] == "support"]
        resistances = [lv for lv in found if lv["kind"] == "resistance"]
        out["nearestSupport"] = max(supports, key=lambda lv: lv["price"]) if supports else None
        out["nearestResistance"] = min(resistances, key=lambda lv: lv["price"]) if resistances else None
        out["nearLevel"] = any(abs(lv["price"] - close) <= a for lv in found)
    if len(bars) > BREAKOUT_BARS:
        window = bars[-BREAKOUT_BARS - 1:-1]
        top, bottom = max(x.high for x in window), min(x.low for x in window)
        out["breakout"] = "up" if close > top else "down" if close < bottom else "none"
    out["candlesticks"] = candlesticks(bars)
    return out
