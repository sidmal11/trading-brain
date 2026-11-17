"""Performance report: how mock trades did (with a hindsight best exit) and how every
alert has done since it was raised. Pure functions: callers pass prices and bars in.

Arithmetic runs on unrounded floats; values are rounded to 2 decimals only in the output.
"""

import logging
import time

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

Bar = tuple[str, float, float, float]  # (date "YYYY-MM-DD", high, low, close)


def _r(x):
    return None if x is None else round(x, 2)


def _extreme(bars, start, end, col, pick):
    """(price, date) of the max/min `col` over bars dated start..end inclusive (end None = latest)."""
    window = [b for b in bars or [] if start and b[0] >= start and (end is None or b[0] <= end)]
    if not window:
        return None, None
    bar = pick(window, key=lambda b: b[col])  # max/min keep the earliest date on ties
    return bar[col], bar[0]


def _mean(values):
    return sum(values) / len(values) if values else None


def trades_report(orders, price_now, bars):
    # Same pairing as TradesStore._open_lots: a FILLED sell closes every open FILLED buy of its ticker.
    open_lots, trades = {}, []
    for o in sorted((o for o in orders if o["status"] == "FILLED"), key=lambda o: o["id"]):
        if o["side"] == "SELL":
            for t in open_lots.pop(o["ticker"], []):
                t["sellPrice"], t["sellDate"] = o["fillPrice"], o["tradingDate"]
        else:
            t = {"orderId": o["id"], "ticker": o["ticker"], "quantity": o["quantity"], "buyPrice": o["fillPrice"],
                 "buyDate": o["tradingDate"], "sellPrice": None, "sellDate": None}
            open_lots.setdefault(o["ticker"], []).append(t)
            trades.append(t)

    for t in trades:
        t["open"] = t["sellPrice"] is None
        exit_price = price_now.get(t["ticker"]) if t["open"] else t["sellPrice"]
        qty, buy = t["quantity"], t["buyPrice"]
        t["exitPrice"] = exit_price
        t["pnl"] = None if exit_price is None else (exit_price - buy) * qty
        t["pnlPercent"] = None if exit_price is None else (exit_price - buy) / buy * 100
        t["bestHigh"], t["bestHighDate"] = _extreme(bars.get(t["ticker"]), t["buyDate"], t["sellDate"], 1, max)
        t["bestExitPnl"] = None if t["bestHigh"] is None else (t["bestHigh"] - buy) * qty
        t["missedPnl"] = None if t["bestExitPnl"] is None or t["pnl"] is None else t["bestExitPnl"] - t["pnl"]

    closed = [t["pnl"] for t in trades if not t["open"]]
    open_trades = [t for t in trades if t["open"]]
    valued = [t for t in open_trades if t["exitPrice"] is not None]
    wins, losses = [p for p in closed if p > 0], [p for p in closed if p < 0]
    totals = {
        "moneyIn": _r(sum(t["buyPrice"] * t["quantity"] for t in open_trades)),
        "currentValue": _r(sum(t["exitPrice"] * t["quantity"] for t in valued)),
        "realizedPnl": _r(sum(closed)),
        "unrealizedPnl": _r(sum(t["pnl"] for t in valued)),
        "closedTrades": len(closed),
        "winRate": _r(len(wins) / len(closed) * 100) if closed else None,
        "averageWin": _r(_mean(wins)),
        "averageLoss": _r(_mean(losses)),
    }
    money = ("buyPrice", "sellPrice", "exitPrice", "pnl", "pnlPercent", "bestHigh", "bestExitPnl", "missedPnl")
    out = [{k: _r(v) if k in money else v for k, v in t.items()} for t in reversed(trades)]
    return {"totals": totals, "trades": out}


def _groups(items, key):
    names = sorted({i[key] for i in items}, key=str)
    return [{"group": n, "count": len(g := [i for i in items if i[key] == n]),
             "averageChangePercent": _r(_mean([i["_change"] for i in g if i["_change"] is not None]))}
            for n in names]


def recommendations_report(alerts, price_now, bars):
    items = []
    for a in alerts:
        start, at = a.get("tradingDate"), a.get("priceAtAlert")
        if not start or at is None:
            continue  # predates phase 1
        now = price_now.get(a["ticker"])
        change = None if now is None else (now - at) / at * 100
        best, best_date = _extreme(bars.get(a["ticker"]), start, None, 1, max)
        worst, worst_date = _extreme(bars.get(a["ticker"]), start, None, 2, min)
        items.append({"alertId": a["alertId"], "ticker": a["ticker"], "strategy": a.get("strategy"),
                      "decision": a.get("status"), "tradingDate": start, "priceAtAlert": _r(at),
                      "priceNow": _r(now), "changePercent": _r(change), "bestPrice": _r(best),
                      "bestDate": best_date, "worstPrice": _r(worst), "worstDate": worst_date, "_change": change})
    items.sort(key=lambda i: i["tradingDate"], reverse=True)
    by_decision, by_strategy = _groups(items, "decision"), _groups(items, "strategy")
    for i in items:
        del i["_change"]
    return {"items": items, "byDecision": by_decision, "byStrategy": by_strategy}


_BARS_TTL = 300
_bars_cache = {}  # (sorted tickers, start) -> (monotonic time, result)


def daily_bars(tickers, start):
    """{ticker: [(date, high, low, close)]} from Yahoo daily history. Any failure gives {}."""
    tickers = sorted(set(tickers))
    key = (tuple(tickers), start)
    hit = _bars_cache.get(key)
    if hit and time.monotonic() - hit[0] < _BARS_TTL:
        return hit[1]
    try:
        # timeout keeps a slow Yahoo inside the dashboard's 8s request budget
        frame = yf.download(tickers, start=start, interval="1d", group_by="ticker", threads=False,
                            progress=False, auto_adjust=False, timeout=5)
        result = {}
        for t in tickers:
            if isinstance(frame.columns, pd.MultiIndex):
                if t not in frame.columns.get_level_values(0):
                    continue
                sub = frame[t]
            elif len(tickers) == 1:  # a single ticker may come back without the ticker level
                sub = frame
            else:
                continue
            sub = sub[["High", "Low", "Close"]].dropna()
            result[t] = [(d.strftime("%Y-%m-%d"), float(h), float(lo), float(c))
                         for d, h, lo, c in zip(sub.index, sub["High"], sub["Low"], sub["Close"])]
    except Exception:
        logger.exception("Yahoo daily bars failed")
        return {}
    _bars_cache[key] = (time.monotonic(), result)
    return result
