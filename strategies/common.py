"""Shared result shape for the series strategies (trend, breakout, mean reversion)."""


def item(key, label, passed, points, detail):
    return {"key": key, "label": label, "passed": bool(passed), "points": points, "detail": detail}


def _money(x):
    return None if x is None else round(x, 2)


def finish(strategy, version, signal_type, as_of, items, buy_keys, entry, stop, target, **extra):
    """One result: score from the passed checks; bullish only when the BUY checks pass and stop < entry < target."""
    passed = {x["key"] for x in items if x["passed"]}
    tradable = None not in (entry, stop, target) and stop < entry < target
    bullish = all(k in passed for k in buy_keys) and tradable
    return {
        "strategy": strategy, "version": version, "signalType": signal_type, "asOf": as_of,
        "direction": "bullish" if bullish else "neutral",
        "confidence": sum(x["points"] for x in items if x["passed"]),
        "checklist": items,
        "evidence": [x["label"] for x in items if x["passed"]],
        "contradictions": [x["label"] for x in items if not x["passed"]],
        "entryPrice": _money(entry), "stopPrice": _money(stop), "targetPrice": _money(target),
        **extra,
    }


def not_enough(strategy, version, signal_type, as_of, labels):
    """Neutral result for a bar without enough history for the indicators."""
    items = [item(k, label, False, pts, "Not enough history") for k, label, pts in labels]
    return finish(strategy, version, signal_type, as_of, items, [k for k, _, _ in labels], None, None, None)


def spy_trend_by_date(spy):
    """{date: "bull" | "bear" | "sideways"} for SPY: close vs its 200-day average and that average's 20-bar slope."""
    from indicators import sma

    if not spy:
        return {}
    close = [b.close for b in spy]
    s200 = sma(close, 200)
    out = {}
    for i in range(220, len(spy)):
        rising, above = s200[i] > s200[i - 20], close[i] > s200[i]
        out[spy[i].date] = "bull" if above and rising else "bear" if not above and not rising else "sideways"
    return out
