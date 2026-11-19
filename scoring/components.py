"""One 0-100 score per scoring component, or None when its inputs are missing (never a made-up 0 or 50).

Each returns (score, detail). Checks follow docs/superpowers/plans/2026-11-19-r6-scoring.md.
"""

BULLISH_CANDLES = {"bullish_engulfing", "hammer", "morning_star"}


def _checks(checks):
    """[(passed, points, text)] -> (score out of the points, "has: ...; lacks: ..." so a low score says what is missing)."""
    total = sum(p for _, p, _ in checks)
    got = sum(p for ok, p, _ in checks if ok)
    has, lacks = [t for ok, _, t in checks if ok], [t for ok, _, t in checks if not ok]
    parts = ([f"has {', '.join(has)}"] if has else []) + ([f"lacks {', '.join(lacks)}"] if lacks else [])
    return round(100 * got / total), "; ".join(parts)


def strategy_component(result):
    """A strategy's own score, or None when it had too little history."""
    if not result or all(x["detail"] == "Not enough history" for x in result["checklist"]):
        return None, "not enough history"
    return result["confidence"], f"{result['confidence']} points ({result['direction']})"


def momentum(f):
    m, rs = f["momentum"], f["relativeStrength"]["vsSpy"]
    if m["rsi14"] is None or m["macdHist"] is None or m["roc20"] is None:
        return None, "not enough history"
    return _checks([
        (55 <= m["rsi14"] <= 70, 20, f"RSI 55-70 (now {m['rsi14']:.0f})"),
        (m["macdHist"] > 0 and (m["macdHistSlope"] or 0) > 0, 25, "MACD histogram positive and rising"),
        (m["roc20"] > 0 and (rs["20"] or 0) > 0, 25, "20-day return positive and ahead of SPY"),
        (m["stochRsi14"] is not None and m["stochRsi14"] > 50, 15, "Stochastic RSI above 50"),
        ((m["rsiSlope5"] or 0) > 0, 15, "RSI rising"),
    ])


def price_action(f):
    pa, atr_pct = f["priceAction"], f["volatility"]["atrPct14"]
    if pa["structure"] is None or atr_pct is None:
        return None, "not enough swings"
    res = pa["nearestResistance"]
    room = res is None or res["distancePct"] >= 2 * atr_pct
    return _checks([
        (pa["structure"] == "up", 40, "higher highs and higher lows"),
        (pa["breakOfStructure"] != "bearish", 20, "structure not broken down"),
        (bool(BULLISH_CANDLES & set(pa["candlesticks"])), 20, "bullish candle"),
        (room, 20, "room to the next resistance" if res else "no resistance above"),
    ])


def volume(f):
    v, t = f["volume"], f["trend"]
    if v["obvTrend20"] is None or v["cmf20"] is None:
        return None, "volume missing"
    return _checks([
        (v["obvTrend20"] > 0.2, 25, "OBV rising"),
        (v["cmf20"] > 0.05, 20, f"money flow above 0.05 (now {v['cmf20']:+.2f})"),
        (v["mfi14"] is not None and 50 <= v["mfi14"] <= 80, 15, "MFI 50-80"),
        (v["relVolume20"] is not None and v["relVolume20"] >= 1.5, 20, "volume at least 1.5x average"),
        ((t["priceVsSma50Pct"] or -1) > 0, 20, "above the 50-day average"),
    ])


def relative_strength(f):
    rs = f["relativeStrength"]
    spy20, spy60, sector20 = rs["vsSpy"]["20"], rs["vsSpy"]["60"], rs["vsSector"]["20"]
    if spy20 is None:
        return None, "no SPY comparison"
    checks = [(spy20 > 0, 35, f"{spy20:+.1f} points vs SPY over 20 days")]
    if spy60 is not None:
        checks.append((spy60 > 0, 35, f"{spy60:+.1f} vs SPY over 60 days"))
    if sector20 is not None:
        checks.append((sector20 > 0, 30, f"{sector20:+.1f} vs {rs['sectorEtf']} over 20 days"))
    return _checks(checks)


def regime(r):
    if not r or r.get("trend") is None:
        return None, "no market regime"
    score = {"bull": 100, "sideways": 50, "bear": 0}[r["trend"]] - (25 if r.get("volatility") == "high" else 0)
    return max(score, 0), f"{r['trend']} market, {r.get('volatility') or 'unknown'} volatility"


def risk_reward(rr):
    if rr is None:
        return None, "no plan"
    return round(100 * min(max((rr - 1) / 2, 0), 1)), f"{rr:.2f} to 1"


ALIGNMENT_SCORE = {"strong": 100, "partial": 60, "neutral": 30, "conflicting": 0}


def alignment(f):
    tf = f.get("timeframes") or {}
    label = tf.get("alignment")
    if label is None:
        return None, "not enough weekly history"
    return ALIGNMENT_SCORE[label], f"{label}: daily {tf['daily']['trend']}, weekly {tf['weekly']['trend']}"


def _tier(value, good, middling, higher=True):
    """100, 50 or 0 for one metric; None when it is missing (spec 11: missing is never weak)."""
    if value is None:
        return None
    if higher:
        return 100 if value > good else 50 if value > middling else 0
    return 100 if value < good else 50 if value < middling else 0


def _multiple(value, good, middling):
    """A valuation multiple: zero or negative (losses) is weak; lower is better."""
    if value is None:
        return None
    return 0 if value <= 0 else _tier(value, good, middling, higher=False)


def fundamental_metrics(v) -> list:
    """[{key, label, value, score}] for every metric R8 scores; value and score None when Yahoo has none."""
    v = v or {}
    fcf, cap = v.get("freeCashflow"), v.get("marketCap")
    fcf_yield = fcf / cap * 100 if fcf is not None and cap else None
    pct = lambda k: None if v.get(k) is None else v[k] * 100  # noqa: E731
    rows = [
        ("revenueGrowth", "Revenue growth %", pct("revenueGrowth"), _tier(pct("revenueGrowth"), 10, 0)),
        ("earningsGrowth", "Earnings growth %", pct("earningsGrowth"), _tier(pct("earningsGrowth"), 10, 0)),
        ("grossMargins", "Gross margin %", pct("grossMargins"), _tier(pct("grossMargins"), 40, 20)),
        ("operatingMargins", "Operating margin %", pct("operatingMargins"), _tier(pct("operatingMargins"), 15, 5)),
        ("profitMargins", "Net margin %", pct("profitMargins"), _tier(pct("profitMargins"), 10, 3)),
        ("returnOnEquity", "Return on equity %", pct("returnOnEquity"), _tier(pct("returnOnEquity"), 15, 8)),
        ("debtToEquity", "Debt to equity %", v.get("debtToEquity"), _tier(v.get("debtToEquity"), 100, 200, higher=False)),
        ("currentRatio", "Current ratio", v.get("currentRatio"), _tier(v.get("currentRatio"), 1.5, 1)),
        ("freeCashflow", "Free cash flow", fcf, None if fcf is None else 100 if fcf > 0 else 0),
        ("trailingPE", "P/E", v.get("trailingPE"), _multiple(v.get("trailingPE"), 20, 35)),
        ("forwardPE", "Forward P/E", v.get("forwardPE"), _multiple(v.get("forwardPE"), 20, 35)),
        ("priceToSales", "Price to sales", v.get("priceToSalesTrailing12Months"),
         _multiple(v.get("priceToSalesTrailing12Months"), 3, 8)),
        ("evToEbitda", "EV/EBITDA", v.get("enterpriseToEbitda"), _multiple(v.get("enterpriseToEbitda"), 12, 20)),
        ("fcfYield", "Free cash flow yield %", fcf_yield, _tier(fcf_yield, 4, 2)),
    ]
    return [{"key": k, "label": label, "value": value, "score": score} for k, label, value, score in rows]


MIN_FUNDAMENTALS = 5


def fundamental(values):
    """Mean of the available metric scores; None with fewer than MIN_FUNDAMENTALS (current values, not point-in-time)."""
    scored = [m for m in fundamental_metrics(values) if m["score"] is not None]
    if len(scored) < MIN_FUNDAMENTALS:
        return None, f"only {len(scored)} fundamentals available"
    strong = [m["label"] for m in scored if m["score"] == 100]
    weak = [m["label"] for m in scored if m["score"] == 0]
    parts = ([f"strong {', '.join(strong)}"] if strong else []) + ([f"weak {', '.join(weak)}"] if weak else [])
    return round(sum(m["score"] for m in scored) / len(scored)), "; ".join(parts) or "all middling"
