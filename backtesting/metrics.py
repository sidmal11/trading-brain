"""Backtest metrics. Trade metrics use closed trades only; equity metrics mark every held day to its close.

Each trade weighs `weight` of capital (notional / capital) and trades do not compound into each other,
so the equity curve answers "what did following every signal at a fixed size do", not a sized portfolio.
"""
import math

DAYS_PER_YEAR = 252
_NONE = {"totalReturn": None, "annualReturn": None, "maxDrawdown": None, "sharpe": None, "sortino": None}


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def trade_stats(trades) -> dict:
    closed = [t["ret"] for t in trades if t.get("ret") is not None]
    if not closed:
        return {"trades": 0, "winRate": None, "avgWin": None, "avgLoss": None, "expectancy": None,
                "profitFactor": None, "avgBarsHeld": None}
    wins = [r for r in closed if r > 0]
    losses = [r for r in closed if r <= 0]
    lost = -sum(losses)
    return {
        "trades": len(closed),
        "winRate": len(wins) / len(closed),
        "avgWin": _mean(wins),
        "avgLoss": _mean(losses),
        "expectancy": _mean(closed),
        "profitFactor": sum(wins) / lost if lost > 0 else None,  # no losing trades: undefined, not infinite
        "avgBarsHeld": _mean([t["bars_held"] for t in trades if t.get("ret") is not None]),
    }


def _series(returns) -> dict:
    if not returns:
        return dict(_NONE)
    equity = peak = 1.0
    drawdown = 0.0
    for r in returns:
        equity *= 1 + r
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - 1)
    years = len(returns) / DAYS_PER_YEAR
    mean = _mean(returns)
    std = math.sqrt(sum((r - mean) ** 2 for r in returns) / len(returns))
    down = math.sqrt(sum(min(r, 0) ** 2 for r in returns) / len(returns))
    return {
        "totalReturn": equity - 1,
        "annualReturn": equity ** (1 / years) - 1 if equity > 0 else None,
        "maxDrawdown": drawdown,
        "sharpe": mean / std * math.sqrt(DAYS_PER_YEAR) if std > 0 else None,
        "sortino": mean / down * math.sqrt(DAYS_PER_YEAR) if down > 0 else None,
    }


def equity_stats(trades, bars_by_ticker, dates, weight) -> dict:
    """Daily equity over `dates` from closed trades, each marked to its ticker's close while held."""
    if not dates:
        return {**_NONE, "exposure": None, "turnover": None}
    daily = {d: 0.0 for d in dates}
    held = set()
    closed = [t for t in trades if t.get("ret") is not None]
    for t in closed:
        bars = bars_by_ticker[t["ticker"]]
        prev = t["entry"]
        for i in range(t["entry_index"], t["exit_index"] + 1):
            price = t["exit"] if i == t["exit_index"] else bars[i].close
            day = bars[i].date
            if day in daily:
                daily[day] += weight * (price / prev - 1)
                held.add(day)
            prev = price
        exit_day = bars[t["exit_index"]].date
        if exit_day in daily:  # commission is in ret but not in the price path
            daily[exit_day] += weight * (t["ret"] - (t["exit"] / t["entry"] - 1))
    years = len(dates) / DAYS_PER_YEAR
    return {**_series([daily[d] for d in dates]), "exposure": len(held) / len(dates),
            "turnover": len(closed) / years}


def buy_and_hold(bars, dates) -> dict:
    """The benchmark (SPY) held through `dates`, on adjusted closes (dividends included)."""
    price = {b.date: (b.adj_close if b.adj_close is not None else b.close) for b in bars}
    days = [d for d in dates if d in price]
    return _series([price[b] / price[a] - 1 for a, b in zip(days, days[1:])])
