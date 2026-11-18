"""One long trade on daily bars, used by the backtest and by live outcome tracking alike.

Rules (docs/superpowers/plans/2026-11-18-r2-backtest-outcomes.md): signal on bar t; enter at bar t+1's open
unless it opens at or below the stop or more than max_chase_pct above bar t's close (a live request would
have expired). From then on a gap through the stop or target fills at the open, a bar touching both counts
as the stop, and the trade ends at the close of bar t+max_hold. Daily bars cannot show intraday order.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    slippage_bps: float = 5.0  # per side (owner, Q8)
    commission: float = 0.0  # dollars per order (owner, Q8)
    notional: float = 500.0  # dollars per trade, to turn commission into a return


def simulate(bars, t, stop, target, max_hold=20, max_chase_pct=3.0, cost=CostModel(), entry_price=None):
    """The trade for a signal on bar `t`, or None when it is never entered.

    With `entry_price` (a live alert) the trade starts at that price on bar t and is walked from t+1.
    Returns entry and exit (after slippage), reason "stop" | "target" | "time" | "open", ret (None while open),
    mfe and mae (best and worst price against the raw entry), bars_held.
    """
    if t + 1 >= len(bars) and entry_price is None:
        return None
    if entry_price is None:
        raw_entry, entry_index = bars[t + 1].open, t + 1
        if raw_entry <= stop or raw_entry > bars[t].close * (1 + max_chase_pct / 100):
            return None
    else:
        raw_entry, entry_index = entry_price, t
    slip = cost.slippage_bps / 10_000
    entry = raw_entry * (1 + slip)
    last = t + max_hold
    high, low = raw_entry, raw_entry
    for i in range(t + 1, min(last, len(bars) - 1) + 1):
        bar = bars[i]
        gap = i > entry_index  # the entry bar opens at the entry price, so it cannot gap
        if gap and bar.open <= stop:
            raw_exit, reason = bar.open, "stop"
        elif gap and bar.open >= target:
            raw_exit, reason = bar.open, "target"
        elif bar.low <= stop:
            raw_exit, reason = stop, "stop"
        elif bar.high >= target:
            raw_exit, reason = target, "target"
        elif i == last:
            raw_exit, reason = bar.close, "time"
        else:
            high, low = max(high, bar.high), min(low, bar.low)
            continue
        if reason == "time":  # held through the whole bar
            high, low = max(high, bar.high), min(low, bar.low)
        else:  # a stop or target bar counts only its open and the exit price: the rest came after the exit
            high, low = max(high, bar.open, raw_exit), min(low, bar.open, raw_exit)
        exit_ = raw_exit * (1 - slip)
        return _trade(bars, entry_index, entry, i, exit_, reason,
                      exit_ / entry - 1 - 2 * cost.commission / cost.notional, high, low, raw_entry)
    return _trade(bars, entry_index, entry, None, None, "open", None, high, low, raw_entry)


def _trade(bars, entry_index, entry, exit_index, exit_, reason, ret, high, low, raw_entry):
    return {
        "entry_index": entry_index, "entry_date": bars[entry_index].date if entry_index >= 0 else None,
        "entry": entry,
        "exit_index": exit_index, "exit_date": None if exit_index is None else bars[exit_index].date,
        "exit": exit_, "reason": reason, "ret": ret,
        "mfe": high / raw_entry - 1, "mae": low / raw_entry - 1,
        "bars_held": None if exit_index is None else exit_index - entry_index + 1,
    }
