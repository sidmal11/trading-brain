"""Daily bars from Yahoo Finance (yfinance). Unofficial; any failure gives {}."""
import logging

import pandas as pd
import yfinance as yf

from market_data.bars import Bar

logger = logging.getLogger(__name__)


def _num(x):
    return None if pd.isna(x) else float(x)


def download_daily(tickers, start=None, period=None) -> dict:
    """{ticker: [Bar]} oldest first. Close is split adjusted; adj_close is also dividend adjusted.

    Rows missing open, high, low or close are dropped and logged, never filled.
    """
    tickers = sorted(set(tickers))
    if not tickers:
        return {}
    try:
        frame = yf.download(tickers, start=start, period=None if start else period, interval="1d",
                            group_by="ticker", threads=True, progress=False, auto_adjust=False, timeout=20)
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
            full = sub.dropna(subset=["Open", "High", "Low", "Close"])
            dropped = len(sub.dropna(how="all")) - len(full)
            if dropped:
                logger.warning("Yahoo: %s rows without prices dropped for %s", dropped, t)
            rows = [Bar(d.strftime("%Y-%m-%d"), float(r["High"]), float(r["Low"]), float(r["Close"]),
                        float(r["Open"]), _num(r["Volume"]), _num(r["Adj Close"]))
                    for d, r in full.iterrows()]
            if rows:
                result[t] = rows
    except Exception:
        logger.exception("Yahoo daily download failed")
        return {}
    return result
