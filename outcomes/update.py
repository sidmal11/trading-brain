"""Live signal outcomes: every alert walked forward with the backtest's own exit rules.

Entry is the alert's price on its trading date; completed bars after that date decide the exit
(stop, target, or the close 20 bars later). Re-measured each day until closed.
"""
from backtesting.simulator import CostModel, simulate


def outcome_rows(alerts, bars_by_ticker, cost=CostModel()) -> list:
    rows = []
    for a in alerts:
        price, day = a.get("priceAtAlert"), a.get("tradingDate")
        stop, target = a.get("stopPrice"), a.get("targetPrice")
        if None in (price, day, stop, target):
            continue  # no stop or target: nothing defines a win or a loss
        bars = bars_by_ticker.get(a["ticker"], [])
        after = next((i for i, b in enumerate(bars) if b.date > day), len(bars))
        row = {"alertId": a["alertId"], "ticker": a["ticker"], "strategy": a.get("strategy") or "volume",
               "signalDate": day, "entry": float(price), "stop": float(stop), "target": float(target),
               "reason": "open", "exitDate": None, "exit": None, "ret": None, "mfe": None, "mae": None,
               "barsHeld": None}
        if after < len(bars):  # the alert day's own bar is not needed: entry is the alert price
            tr = simulate(bars, after - 1, float(stop), float(target), cost=cost, entry_price=float(price))
            row.update(reason=tr["reason"], exitDate=tr["exit_date"], exit=tr["exit"], ret=tr["ret"],
                       mfe=tr["mfe"], mae=tr["mae"], barsHeld=tr["bars_held"], entry=tr["entry"])
        rows.append(row)
    return rows
