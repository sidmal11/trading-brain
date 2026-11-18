"""MACD strategy maths: indicators and the five-condition checklist.

Bars are (date "YYYY-MM-DD", high, low, close), oldest first, completed bars
only. Every window is counted back from the last bar in the list.
"""

MIN_BARS = 230  # 200 for SMA200 plus 30 bars of cross history

ITEMS = [
    ("uptrend", "Uptrend: above a rising 200-day average, not choppy", 25),
    ("macd", "MACD crossed up in the last 3 days", 25),
    ("volume", "Unusual volume", 20),
    ("price_action", "Breakout or pullback", 15),
    ("reward_risk", "Stop within 8%, target 1.5x the risk", 15),
]
BUY_KEYS = ("uptrend", "macd", "price_action", "reward_risk")


def sma(values, n):
    """Simple moving average; None until n values. A running window sum, so a 5-year backtest stays fast."""
    out = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def ema(values, n):
    """pandas ewm(span=n, adjust=False): alpha 2/(n+1), seeded with the first value."""
    alpha = 2 / (n + 1)
    out = []
    for v in values:
        out.append(v if not out else alpha * v + (1 - alpha) * out[-1])
    return out


def macd(closes):
    line = [a - b for a, b in zip(ema(closes, 12), ema(closes, 26))]
    signal = ema(line, 9)
    return line, signal, [m - s for m, s in zip(line, signal)]


def _plural(k, word):
    return f"{k} {word}" if k == 1 else f"{k} {word}es"


def _uptrend(closes):
    s = sma(closes, 200)
    above = [c > m for c, m in zip(closes[-31:], s[-31:])]
    crosses = sum(above[i] != above[i - 1] for i in range(1, 31))  # crosses landing on the last 30 bars
    passed = closes[-1] > s[-1] and s[-1] > s[-21] and crosses < 3
    detail = (f"Close {closes[-1]:.2f} vs 200-day {s[-1]:.2f}, 20 days ago {s[-21]:.2f}; "
              f"{_plural(crosses, 'cross')} in 30 days")
    return passed, detail


def _macd(closes):
    line, sig, hist = macd(closes)
    n = len(closes)
    ago = next((n - 1 - i for i in range(n - 1, n - 4, -1)
                if line[i - 1] <= sig[i - 1] and line[i] > sig[i]), None)
    passed = ago is not None and hist[-1] > 0 and hist[-1] > hist[-2]
    when = "no cross up in 3 days" if ago is None else (
        "crossed up on the last day" if ago == 0 else f"crossed up {ago} {'day' if ago == 1 else 'days'} ago")
    detail = (f"MACD {line[-1]:.3g} vs signal {sig[-1]:.3g}, {when}; "
              f"histogram {hist[-1]:.3g} (previous {hist[-2]:.3g})")
    return passed, detail


def _price_action(bars, closes):
    last = closes[-1]
    high20 = max(b[1] for b in bars[-21:-1])
    breakout = high20 < last <= 1.03 * high20
    e20 = ema(closes, 20)
    n = len(bars)
    pull = next((i for i in range(n - 6, n - 1) if bars[i][2] <= e20[i] and last > closes[i]), None)
    detail = f"Close {last:.2f} vs 20-day high {high20:.2f} (breakout up to {1.03 * high20:.2f}); "
    detail += ("no pullback to EMA20 in 5 days" if pull is None else
               f"pullback on {bars[pull][0]}: low {bars[pull][2]:.2f} vs EMA20 {e20[pull]:.2f}, "
               f"close then {closes[pull]:.2f}")
    return breakout or pull is not None, detail


def _reward_risk(bars, entry):
    stop = min(b[2] for b in bars[-10:])
    if stop >= entry:
        return False, f"Entry {entry:.2f}, 10-day low {stop:.2f} is not below entry", None, None
    risk = (entry - stop) / entry
    target = entry + 1.5 * (entry - stop)
    detail = f"Entry {entry:.2f}, stop {stop:.2f} ({risk * 100:.2f}% risk), target {target:.2f}"
    return risk <= 0.08, detail, stop, target


def _money(x):
    return None if x is None else round(x, 2)


def checklist(bars, entry, volume_ok, volume_detail):
    closes = [b[3] for b in bars]
    if entry is None and closes:
        entry = closes[-1]
    if len(bars) < MIN_BARS:
        results = {k: (False, "Not enough history") for k, _, _ in ITEMS}
        results["volume"] = (bool(volume_ok), volume_detail)  # volume comes from the scan, not history
        stop = target = None
    else:
        rr_passed, rr_detail, stop, target = _reward_risk(bars, entry)
        results = {
            "uptrend": _uptrend(closes),
            "macd": _macd(closes),
            "volume": (bool(volume_ok), volume_detail),
            "price_action": _price_action(bars, closes),
            "reward_risk": (rr_passed, rr_detail),
        }
    items = [{"key": k, "label": label, "passed": results[k][0], "points": pts, "detail": results[k][1]}
             for k, label, pts in ITEMS]
    return {
        "checklist": items,
        "confidence": sum(x["points"] for x in items if x["passed"]),
        "entryPrice": _money(entry),
        "stopPrice": _money(stop),
        "targetPrice": _money(target),
    }


def macd_buy(result):
    passed = {x["key"] for x in result["checklist"] if x["passed"]}
    return all(k in passed for k in BUY_KEYS)
