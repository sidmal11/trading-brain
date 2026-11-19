"""Replay a live strategy over stored daily bars, simulate every signal, store the run.

Each decision on bar t calls the same strategy function the poll calls, on the same 500-bar window
(strategies.history.HISTORY_BARS) ending at t, so no later bar can reach it.

Command: python -m backtesting.runner [volume|macd]   (needs STORAGE_BACKEND=sqlite and stored bars)
"""
import argparse
import json
import logging
from datetime import date, timedelta

from backtesting.metrics import buy_and_hold, equity_stats, trade_stats
from backtesting.simulator import CostModel, simulate
from strategies import accumulation, breakout, macd, mean_reversion, momentum, pullback, squeeze, trend, volume
from strategies.history import HISTORY_BARS

logger = logging.getLogger(__name__)

BACKTEST_VERSION = "1"
VOLUME_THRESHOLD = 3.0  # the live default
HOLDOUT_DAYS = 365  # owner, Q9: the last 12 months are out of sample
CAPITAL = 10_000.0  # the mock account's starting cash; each trade is CostModel.notional of it
MODULES = {"volume": volume, "macd": macd, "trend": trend, "breakout": breakout, "mean_reversion": mean_reversion,
           "momentum": momentum, "pullback": pullback, "accumulation": accumulation, "squeeze": squeeze}
SERIES = {k: m for k, m in MODULES.items() if hasattr(m, "evaluate_series")}  # whole-history strategies
COOLDOWN = {"volume": 0, "macd": 3}  # live: one MACD alert per crossover, which stays "recent" for 3 bars
CAVEATS = [
    "Today's large caps only: companies that failed or left the list are missing, so results lean high.",
    "Daily bars: the order of moves inside a day is unknown; a day touching both stop and target counts as the stop.",
    "Volume uses the completed day over the previous 20 days; live alerts use a projection during the day.",
    "Each signal is a fixed $500 of $10,000; trades do not compound.",
]


def _volume_score(window):
    vols = [b.volume for b in window[-21:-1]]
    if len(vols) < 20 or None in vols or window[-1].volume is None:
        return None
    avg = sum(vols) / len(vols)
    return round(window[-1].volume / avg, 2) if avg > 0 else None


def decide(strategy, ticker, window, threshold=VOLUME_THRESHOLD):
    """The live strategy's result for the last bar of `window`, or None."""
    score = _volume_score(window)
    if strategy == "volume":
        return None if score is None else volume.evaluate(ticker, window, {"volume_score": score, "price": None},
                                                          threshold)
    return macd.evaluate(ticker, window, score, threshold)


def new_setups(results):
    """Indexes where a setup appears: bullish on this bar and not on the one before (live requests use the same rule)."""
    return [t for t in range(1, len(results))
            if results[t]["direction"] == "bullish" and results[t - 1]["direction"] != "bullish"]


def _replay_series(module, ticker, bars, cost, spy):
    results = module.evaluate_series(bars, spy)
    trades, skipped, free_from = [], 0, 0
    for t in new_setups(results):
        if t >= len(bars) - 1 or t < free_from:
            continue
        r = results[t]
        trade = simulate(bars, t, r["stopPrice"], r["targetPrice"], max_hold=module.MAX_HOLD, cost=cost)
        if trade is None:
            skipped += 1
            continue
        trades.append({**trade, "ticker": ticker, "signal_date": bars[t].date})
        free_from = trade["exit_index"] + 1 if trade["exit_index"] is not None else len(bars)
    return trades, skipped


def replay(strategy, ticker, bars, cost=CostModel(), spy=None):
    """(trades, skipped) for one ticker: one open trade at a time; skipped = signals never entered."""
    if strategy in SERIES:
        return _replay_series(SERIES[strategy], ticker, bars, cost, spy)
    trades, skipped = [], 0
    free_from, last_signal = 0, None
    for t in range(HISTORY_BARS - 1, len(bars) - 1):
        if t < free_from or (last_signal is not None and t - last_signal <= COOLDOWN[strategy]):
            continue
        sig = decide(strategy, ticker, bars[t - HISTORY_BARS + 1:t + 1])
        if not sig or sig.get("stopPrice") is None or sig.get("targetPrice") is None:
            continue
        last_signal = t
        trade = simulate(bars, t, sig["stopPrice"], sig["targetPrice"], cost=cost)
        if trade is None:
            skipped += 1
            continue
        trades.append({**trade, "ticker": ticker, "signal_date": bars[t].date})
        free_from = trade["exit_index"] + 1 if trade["exit_index"] is not None else len(bars)
    return trades, skipped


def run(strategy, bars_by_ticker, spy, cost=CostModel()) -> dict:
    """{"trades", "skipped", "windows": {"inSample", "holdout"}} over every ticker in `bars_by_ticker`."""
    trades, skipped = [], 0
    for ticker in sorted(bars_by_ticker):
        got, missed = replay(strategy, ticker, bars_by_ticker[ticker], cost, spy)
        trades += got
        skipped += missed
    days = [b.date for b in spy]
    first = getattr(MODULES[strategy], "WARMUP", HISTORY_BARS - 1)
    starts = [bars[first].date for bars in bars_by_ticker.values() if len(bars) > first]
    windows = {}
    if days and starts:
        cut = (date.fromisoformat(days[-1]) - timedelta(days=HOLDOUT_DAYS)).isoformat()
        for name, in_window in (("inSample", lambda d: min(starts) <= d < cut), ("holdout", lambda d: d >= cut)):
            dates = [d for d in days if in_window(d)]
            picked = [t for t in trades if in_window(t["signal_date"])]
            windows[name] = {
                "from": dates[0] if dates else None, "to": dates[-1] if dates else None,
                **trade_stats(picked),
                **equity_stats(picked, bars_by_ticker, dates, cost.notional / CAPITAL),
                "stillOpen": sum(t["ret"] is None for t in picked),
                "benchmark": buy_and_hold(spy, dates),
            }
    return {"trades": trades, "skipped": skipped, "windows": windows}


def run_and_store(bar_store, research_store, strategy, tickers, today: str, cost=CostModel()) -> int:
    bars = bar_store.daily(list(tickers) + ["SPY"], before=today)
    spy = bars.pop("SPY", [])
    result = run(strategy, {t: b for t, b in bars.items() if t in tickers}, spy, cost)
    params = {"historyBars": HISTORY_BARS, "volumeThreshold": VOLUME_THRESHOLD, "holdoutDays": HOLDOUT_DAYS,
              "maxHold": getattr(MODULES[strategy], "MAX_HOLD", 20), "maxChasePct": 3.0,
              "slippageBps": cost.slippage_bps, "commission": cost.commission,
              "notional": cost.notional, "capital": CAPITAL, "cooldownBars": COOLDOWN.get(strategy, 0),
              "signalRule": "new setup only" if strategy in SERIES else "every signal",
              "tickers": len(tickers), "skippedEntries": result["skipped"], "caveats": CAVEATS}
    return research_store.save_run(strategy, MODULES[strategy].VERSION, BACKTEST_VERSION, params,
                                   result["windows"], result["trades"])


def main():
    from config import Config
    from market_data.bar_store import BarStore
    from backtesting.store import ResearchStore
    from utils.universe import universe_tickers
    from utils.market_clock import trading_date

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("strategy", choices=sorted(MODULES))
    args = parser.parse_args()
    store = ResearchStore(Config.SQLITE_PATH)
    run_id = run_and_store(BarStore(Config.SQLITE_PATH), store, args.strategy, universe_tickers(), trading_date())
    print(json.dumps(store.latest_run(args.strategy)["result"], indent=2))
    print(f"run {run_id} stored")


if __name__ == "__main__":
    main()
