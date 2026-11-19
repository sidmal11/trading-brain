"""Risk view of one opportunity: the plan (stop, target), reward/risk, volatility, liquidity, gap risk, costs."""
from indicators import atr

DEFAULT_STOP_ATR = 1.5
COST_PCT = 0.10  # 5 basis points slippage each way (owner, Q8); commission $0
GAP_LOOKBACK = 250


def best_plan(results):
    """The highest-scoring bullish strategy's (name, result), or None."""
    bullish = [(k, r) for k, r in results.items() if r and r.get("direction") == "bullish"]
    return max(bullish, key=lambda kr: kr[1]["confidence"]) if bullish else None


def assess(bars, results, regime=None, max_holds=None) -> dict:
    high, low, close = [b.high for b in bars], [b.low for b in bars], [b.close for b in bars]
    a = atr(high, low, close, 14)[-1] if len(bars) >= 14 else None
    entry = close[-1] if bars else None
    plan = best_plan(results)
    if plan:
        source, r = plan
        stop, target = r["stopPrice"], r["targetPrice"]
    elif a is not None:
        source, stop = "default", entry - DEFAULT_STOP_ATR * a
        target = entry + 2 * (entry - stop)
    else:
        source = stop = target = None
    rr = (target - entry) / (entry - stop) if None not in (stop, target, entry) and entry > stop else None
    window = bars[-20:]
    dollar_volume = (sum(b.close * b.volume for b in window) / len(window)
                     if len(window) == 20 and None not in [b.volume for b in window] else None)
    gaps = [abs(bars[i].open - bars[i - 1].close) for i in range(max(1, len(bars) - GAP_LOOKBACK), len(bars))]
    big_gap = (sum(g > a for g in gaps) / len(gaps) * 100) if a and gaps else None
    elevated = bool(regime) and (regime.get("trend") == "bear" or regime.get("volatility") == "high")
    return {
        "planSource": source, "entry": entry, "stop": stop, "target": target, "riskReward": rr,
        "stopDistancePct": (entry - stop) / entry * 100 if None not in (entry, stop) else None,
        "targetDistancePct": (target - entry) / entry * 100 if None not in (entry, target) else None,
        "atr": a, "atrPct": a / entry * 100 if a and entry else None,
        "dollarVolume20": dollar_volume, "bigGapPct": big_gap, "costPct": COST_PCT,
        "maxHoldDays": (max_holds or {}).get(source, 20), "regimeRisk": "elevated" if elevated else "normal",
    }
