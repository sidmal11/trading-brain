"""Does daily-weekly trend alignment (R7) on the signal day go with better backtest trades?

Reads each strategy's latest stored backtest run, labels every trade with the alignment of its signal day (from
completed bars up to that day only), and reports trade statistics per label, for the whole run and for the
12-month holdout. Research only: it changes no weight; the owner decides whether alignment earns one.

Command: python -m backtesting.alignment_study   (needs STORAGE_BACKEND=sqlite, stored bars and backtest runs)
"""
from bisect import bisect_right

from backtesting.metrics import trade_stats
from features.build import alignment, trend_state
from indicators import sma
from market_data.weekly import weekly_bars

LABELS = ("strong", "partial", "neutral", "conflicting", None)


def labels_by_date(bars) -> dict:
    """{date: alignment label or None} for every daily bar, each from bars up to that date only.

    A past week is complete once its last trading day has passed, which the stored bars themselves show; this
    matches features.build.timeframes for any day the NYSE calendar covers.
    """
    close = [b.close for b in bars]
    s50, s200 = sma(close, 50), sma(close, 200)
    weeks = weekly_bars(bars)
    week_dates = [w.date for w in weeks]
    wc = [w.close for w in weeks]
    w10, w40 = (sma(wc, 10), sma(wc, 40)) if wc else ([], [])
    out = {}
    for t, b in enumerate(bars):
        daily = trend_state(close[t], s50[t], s200[t])
        k = bisect_right(week_dates, b.date) - 1  # the newest week whose last day is on or before this day
        weekly = trend_state(wc[k], w10[k], w40[k]) if k >= 1 else None
        out[b.date] = alignment(daily, weekly)
    return out


def study(research, bar_store, strategies, today) -> dict:
    """{strategy: {"all": {label: stats}, "holdout": {label: stats}}} plus "ALL" pooled over the strategies."""
    pooled = {"all": {lb: [] for lb in LABELS}, "holdout": {lb: [] for lb in LABELS}}
    report, labels_cache = {}, {}
    for strategy in strategies:
        run = research.latest_run(strategy)
        if not run:
            continue
        trades = research.run_trades(run["id"], limit=10**6)
        holdout_from = (run["result"].get("holdout") or {}).get("from")
        groups = {"all": {lb: [] for lb in LABELS}, "holdout": {lb: [] for lb in LABELS}}
        for tr in trades:
            if tr["ticker"] not in labels_cache:
                labels_cache[tr["ticker"]] = labels_by_date(bar_store.daily([tr["ticker"]], before=today).get(tr["ticker"], []))
            label = labels_cache[tr["ticker"]].get(tr["signal_date"])
            for window in ("all", "holdout") if holdout_from and tr["signal_date"] >= holdout_from else ("all",):
                groups[window][label].append(tr)
                pooled[window][label].append(tr)
        report[strategy] = {w: {lb: trade_stats(ts) for lb, ts in g.items()} for w, g in groups.items()}
    report["ALL"] = {w: {lb: trade_stats(ts) for lb, ts in g.items()} for w, g in pooled.items()}
    return report


def markdown(report) -> str:
    def cell(s):
        if not s["trades"]:
            return "-"
        pf = "-" if s["profitFactor"] is None else f"{s['profitFactor']:.2f}"
        return f"{s['trades']} / {s['winRate'] * 100:.0f}% / {s['expectancy'] * 100:+.2f}% / {pf}"

    lines = []
    for window in ("all", "holdout"):
        lines += [f"### {'Whole run' if window == 'all' else 'Last 12 months (holdout)'}", "",
                  "Cells: trades / win rate / average return per trade / profit factor.", "",
                  "| Strategy | Strong | Partial | Neutral | Conflicting | Unknown |", "|---|---|---|---|---|---|"]
        for strategy, r in report.items():
            lines.append(f"| {strategy} | " + " | ".join(cell(r[window][lb]) for lb in LABELS) + " |")
        lines.append("")
    return "\n".join(lines)


def main():
    from config import Config
    from backtesting.runner import MODULES
    from backtesting.store import ResearchStore
    from market_data.bar_store import BarStore
    from utils.market_clock import trading_date

    print(markdown(study(ResearchStore(Config.SQLITE_PATH), BarStore(Config.SQLITE_PATH), sorted(MODULES), trading_date())))


if __name__ == "__main__":
    main()
