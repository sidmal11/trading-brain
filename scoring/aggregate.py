"""Weighted research score from component scores: renormalised over what has data, trend family capped."""
from scoring.profiles import BULLISH_AT, TREND_FAMILY, TREND_FAMILY_CAP, WARN_PENALTY


def aggregate(components, weights, quality_status="valid") -> dict:
    """{"score", "direction", "coverage", "breakdown"} from {name: (score or None, detail)}.

    breakdown rows: component, weight (share of the used weight, 0-1), score, contribution (weight x score).
    """
    present = {k: s for k, (s, _) in components.items() if s is not None and weights.get(k, 0) > 0}
    used = {k: float(weights[k]) for k in present}
    family = sum(used[k] for k in TREND_FAMILY if k in used)
    if family > TREND_FAMILY_CAP:
        for k in TREND_FAMILY:
            if k in used:
                used[k] *= TREND_FAMILY_CAP / family
    enabled = sum(w for w in weights.values() if w > 0)
    coverage = sum(weights[k] for k in present) / enabled if enabled else 0.0
    total = sum(used.values())
    if not total:
        return {"score": None, "direction": "neutral", "coverage": coverage, "breakdown": []}
    score = sum(used[k] * present[k] for k in used) / total
    if quality_status == "warn":
        score *= WARN_PENALTY
    breakdown = sorted(({"component": k, "weight": used[k] / total, "score": present[k],
                         "contribution": used[k] / total * present[k], "detail": components[k][1]} for k in used),
                       key=lambda r: -r["contribution"])
    return {"score": round(score, 1), "direction": "bullish" if score >= BULLISH_AT else "neutral",
            "coverage": coverage, "breakdown": breakdown}
