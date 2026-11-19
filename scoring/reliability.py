"""Strategy reliability (DAE 12/13): how sure the out-of-sample record is that a strategy's trades make money.

Score = 50 + 25 x t, clamped to 0-100, where t is the holdout trades' mean return over its standard error. Few
trades give a small t, so a strategy cannot earn a high score from a short lucky run (spec: "too few historical
observations" must not look reliable); under MIN_TRADES there is no score at all. Weight 0 in every profile until
the owner decides it is earned.
"""
import math

MIN_TRADES = 30


def reliability(returns):
    """(score or None, detail) from holdout trade returns (fractions)."""
    n = len(returns)
    if n < MIN_TRADES:
        return None, f"only {n} holdout trades (needs {MIN_TRADES})"
    mean = sum(returns) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in returns) / (n - 1))
    if sd == 0:
        return None, "holdout returns do not vary"
    t = mean / (sd / math.sqrt(n))
    score = round(min(max(50 + 25 * t, 0), 100))
    return score, f"{n} holdout trades, {mean * 100:+.2f}% each on average, t = {t:.2f}"


def strategy_reliability(research, strategies) -> dict:
    """{strategy: {"score", "detail", "trades"}} from each strategy's latest stored backtest run."""
    out = {}
    for name in strategies:
        run = research.latest_run(name)
        if not run:
            out[name] = {"score": None, "detail": "no backtest yet", "trades": 0}
            continue
        start = (run["result"].get("holdout") or {}).get("from")
        returns = [t["ret"] for t in research.run_trades(run["id"], limit=10**6)
                   if start and t["signal_date"] >= start and t["ret"] is not None]
        score, detail = reliability(returns)
        out[name] = {"score": score, "detail": detail, "trades": len(returns)}
    return out
