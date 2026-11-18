"""Once-a-day refresh of the bar store from the provider, then a data-quality report per ticker."""
import logging
from datetime import date, timedelta

from data_quality.checks import check_bars
from market_data import yahoo

logger = logging.getLogger(__name__)

BACKFILL = "5y"  # owner, Q9
OVERLAP_DAYS = 10  # re-read this much stored history to notice re-adjusted prices
ADJUSTED = 0.005  # an overlapping close off by more than 0.5% means the provider re-adjusted history
REFERENCE = "SPY"


def _changed(old, new):
    stored = {b.date: b.close for b in old}
    return any(abs(b.close - stored[b.date]) > ADJUSTED * stored[b.date] for b in new if b.date in stored)


def refresh_daily(store, today: date, tickers, download=yahoo.download_daily) -> dict:
    """Store completed bars (dated before `today`); returns {"downloaded", "refetched", "missing", "failed"}.

    A failed download writes nothing and leaves the reports as they were. Tickers the provider left out
    (or whose full re-download came back empty) are listed in "missing" so the caller retries later.
    """
    cutoff = today.isoformat()
    tickers = sorted(set(tickers))
    newest = {t: store.newest_date(t) for t in tickers}
    stored = store.daily(tickers, cutoff)
    out = {"downloaded": 0, "refetched": [], "missing": [], "failed": False}

    batches = []
    new = [t for t in tickers if newest[t] is None]
    if new:
        batches.append((new, None, BACKFILL))
    old = [t for t in tickers if newest[t]]
    if old:
        start = date.fromisoformat(min(newest[t] for t in old)) - timedelta(days=OVERLAP_DAYS)
        batches.append((old, start.isoformat(), None))
    for group, start, period in batches:
        got = download(group, start=start, period=period)
        if not got:
            out["failed"] = True
            continue
        out["missing"] += [t for t in group if t not in got]
        for t, rows in got.items():
            rows = [b for b in rows if b.date < cutoff]
            if newest[t] and _changed(stored.get(t, []), rows):
                full = [b for b in download([t], period=BACKFILL).get(t, []) if b.date < cutoff]
                if full:
                    store.replace(t, full)
                    out["refetched"].append(t)
                    logger.info("%s history re-adjusted by the provider; downloaded again", t)
                else:
                    out["missing"].append(t)  # keep the old rows; retry the full download later
                continue
            out["downloaded"] += store.upsert(t, rows)
    if out["failed"]:
        return out
    out["missing"].sort()

    bars = store.daily(tickers, cutoff)
    reference = {b.date for b in bars.get(REFERENCE, [])} or None
    for t in tickers:
        store.save_report(t, check_bars(bars.get(t, []), today, None if t == REFERENCE else reference))
    return out
