"""Scoring weights and risk gates. Starting hypotheses (owner Q10, Q11), changed only with a new SCORE_VERSION."""

SCORE_VERSION = "1"

# DAE 13 starting weights for short-term and swing trading. Reliability (5) and ML (5) stay 0 until earned.
SWING = {
    "trend": 15, "momentum": 12, "price_action": 15, "breakout": 12, "volume": 10,
    "relative_strength": 8, "regime": 8, "risk_reward": 10, "reliability": 0, "ml": 0,
    "alignment": 0,  # R7: shown with every opportunity, counted only after a backtest earns it
}
# Trend, momentum and breakout move together: their combined weight is capped before renormalising.
TREND_FAMILY = ("trend", "momentum", "breakout")
TREND_FAMILY_CAP = 35
WARN_PENALTY = 0.9  # a data-quality warning trims the score

GATES = {"maxStaleTradingDays": 1, "minCoverage": 0.70, "minDollarVolume": 20e6, "maxAtrPct": 8.0, "minRiskReward": 1.5}
BULLISH_AT = 50

LABELS = {
    "trend": "Trend", "momentum": "Momentum", "price_action": "Price action", "breakout": "Breakout",
    "volume": "Volume", "relative_strength": "Relative strength", "regime": "Market regime",
    "risk_reward": "Reward/risk", "reliability": "Strategy reliability", "ml": "Machine learning",
    "alignment": "Timeframe alignment",
}
