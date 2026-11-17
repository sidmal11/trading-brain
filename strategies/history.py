"""Completed daily bars, downloaded once per trading day and cached in the poll process."""
import logging

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

_cache = {}  # {"today": str, "bars": {ticker: [Bar]}}; a later day replaces it


def completed_bars(tickers, today):
    """{ticker: [(date, high, low, close)]}, oldest first, without today's partial bar. Failure gives {}."""
    if _cache.get("today") == today:
        return _cache["bars"]
    try:
        frame = yf.download(tickers, period="2y", interval="1d", group_by="ticker", threads=True,
                            progress=False, auto_adjust=False, timeout=10)
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
            bars = [(d.strftime("%Y-%m-%d"), float(h), float(lo), float(c))
                    for d, h, lo, c in zip(sub.index, sub["High"], sub["Low"], sub["Close"])]
            bars = [b for b in bars if b[0] != today]
            if bars:
                result[t] = bars
    except Exception:
        logger.exception("Yahoo history download failed")
        return {}
    if not result:  # an empty outage frame is a failure: retry on the next call
        return {}
    missing = [t for t in tickers if t not in result]
    if missing:
        logger.warning("Yahoo history missing for %s", ", ".join(missing))
    _cache.update(today=today, bars=result)
    return result
