"""Risk gates and the explanation, both built only from the computed numbers."""
from datetime import date

from market_data.calendar import previous_trading_day
from scoring.profiles import GATES, LABELS

STRATEGY_LABELS = {"macd": "MACD trend", "trend": "Trend following", "breakout": "Breakout",
                   "mean_reversion": "Mean reversion"}


def gates(risk, coverage, quality, newest_bar, today: date) -> list:
    """Failed gates as plain reasons; an empty list means eligible."""
    failed = []
    due = previous_trading_day(today).isoformat()
    if newest_bar is None or newest_bar < due:
        failed.append(f"Stale data: newest bar {newest_bar or 'none'}, expected {due}")
    if quality == "invalid":
        failed.append("Data quality invalid")
    if coverage < GATES["minCoverage"]:
        failed.append(f"Only {coverage * 100:.0f}% of the score has data (needs {GATES['minCoverage'] * 100:.0f}%)")
    dv = risk["dollarVolume20"]
    if dv is None or dv < GATES["minDollarVolume"]:
        failed.append("Average dollar volume unknown" if dv is None
                      else f"Average dollar volume ${dv / 1e6:.0f}M under ${GATES['minDollarVolume'] / 1e6:.0f}M")
    if risk["atrPct"] is not None and risk["atrPct"] > GATES["maxAtrPct"]:
        failed.append(f"Too volatile: ATR {risk['atrPct']:.1f}% of price (max {GATES['maxAtrPct']:g}%)")
    rr = risk["riskReward"]
    if rr is None or rr < GATES["minRiskReward"]:
        failed.append("No usable plan" if rr is None else f"Reward/risk {rr:.2f} under {GATES['minRiskReward']}")
    return failed


def explain(agg, results, risk, failed, warnings, components) -> dict:
    rows = agg["breakdown"]
    positives = [f"{LABELS[r['component']]} {r['score']}: {r['detail']}" for r in rows if r["score"] >= 60]
    negatives = [f"{LABELS[r['component']]} {r['score']}: {r['detail']}" for r in rows if r["score"] < 40]
    agreeing = [STRATEGY_LABELS[k] for k, r in results.items() if r and r.get("direction") == "bullish"]
    others = [f"{STRATEGY_LABELS[k]} ({r['confidence']})" for k, r in results.items()
              if r and r.get("direction") != "bullish"]
    top = [f"{LABELS[r['component']]}: {r['contribution']:.1f} of the score" for r in rows[:3]]
    missing = [f"{LABELS[k]} ({detail})" for k, (s, detail) in components.items() if s is None]
    risks = [f"Plan from {STRATEGY_LABELS.get(risk['planSource'], 'the default 1.5 ATR stop')}"
             if risk["planSource"] else "No plan"]
    if risk["atrPct"] is not None:
        risks.append(f"ATR {risk['atrPct']:.1f}% of price")
    if risk["bigGapPct"] is not None:
        risks.append(f"Gaps over 1 ATR on {risk['bigGapPct']:.0f}% of days in the last year")
    if risk["regimeRisk"] == "elevated":
        risks.append("Market regime raises risk (bear market or high volatility)")
    return {"positives": positives, "negatives": negatives, "agreeing": agreeing, "notAgreeing": others,
            "topContributions": top, "missing": missing, "gatesFailed": failed, "warnings": warnings, "risks": risks}
