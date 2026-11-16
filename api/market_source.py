"""Assembles the shapes `frontend/src/types/market.ts` declares.

The dashboard asks for more than the poll handler stores. A stored alert holds a
ticker, a signal description and the LLM's prose; a `StockSignal` additionally
needs a price, a sector, a market cap, a volume ratio and an intraday series.
That gap is filled here from Yahoo Finance, so the dashboard shows a real quote
next to real analysis rather than a fixture.

Every lookup degrades to None rather than raising. One delisted ticker or one
rate-limited response must not take down the whole dashboard: the caller drops
that row and serves the rest, which the UI then reports as partial rather than
unavailable.
"""

import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# `yfinance` is heavy and only needed when a request actually touches market
# data, so it is imported lazily. A missing install degrades to empty results
# instead of a 500 at import time.
_yf = None
_yf_failed = False


def _yfinance():
    global _yf, _yf_failed
    if _yf is None and not _yf_failed:
        try:
            import yfinance

            _yf = yfinance
        except ImportError:
            logger.error("yfinance is not installed; market data will be unavailable.")
            _yf_failed = True
    return _yf


# ── Classification helpers ────────────────────────────────────────────────────

def market_cap_category(market_cap):
    """Bucket a market cap into the four tiers the filter toolbar offers."""
    if not market_cap:
        return "SMALL"
    if market_cap >= 200_000_000_000:
        return "MEGA"
    if market_cap >= 10_000_000_000:
        return "LARGE"
    if market_cap >= 2_000_000_000:
        return "MID"
    return "SMALL"


def _trend(last, previous, rsi):
    """Label a futures contract's direction.

    RSI alone is a momentum reading, not a direction, so an RSI near the middle
    with a flat price is reported as sideways rather than being forced into a
    trend the chart does not show.
    """
    if rsi is None:
        return "SIDEWAYS"
    if rsi >= 60:
        return "UPTREND"
    if rsi <= 40:
        return "DOWNTREND"
    if last is not None and previous:
        drift = (last - previous) / previous * 100
        if drift > 1:
            return "UPTREND"
        if drift < -1:
            return "DOWNTREND"
    return "SIDEWAYS"


def compute_rsi(closes, period=14):
    """Wilder's RSI over a list of closes, or None when there is too little data.

    Returning None rather than a neutral 50 keeps a thin series honest: 50 is a
    real reading that means balance, and inventing it would put a contract on
    the dashboard claiming a momentum it was never measured to have.
    """
    if not closes or len(closes) <= period:
        return None

    gains = []
    losses = []
    for earlier, later in zip(closes, closes[1:]):
        change = later - earlier
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder smoothing over the remainder of the series.
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


# ── Sentiment parsing ─────────────────────────────────────────────────────────

_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.MULTILINE)


def split_pros_cons(analysis):
    """Pull bullish and bearish bullets out of the LLM's prose.

    The model is asked for a Pros/Cons summary but returns free text, so this
    splits on the headings it actually uses. Anything it cannot parse is left in
    `summary` rather than being discarded, because an unparsed analysis is still
    the most useful thing on the card.
    """
    if not analysis:
        return [], [], ""

    lowered = analysis.lower()
    pros_at = max(lowered.find("pros"), lowered.find("bullish"))
    cons_at = max(lowered.find("cons"), lowered.find("bearish"))

    if pros_at == -1 or cons_at == -1 or cons_at <= pros_at:
        return [], [], analysis.strip()

    pros_block = analysis[pros_at:cons_at]
    cons_block = analysis[cons_at:]

    def bullets(block):
        lines = [_BULLET.sub("", line).strip() for line in block.splitlines()[1:]]
        return [line for line in lines if line]

    return bullets(pros_block), bullets(cons_block), analysis[:pros_at].strip()


def _sentiment(pros, cons):
    if len(pros) > len(cons):
        return "BULLISH"
    if len(cons) > len(pros):
        return "BEARISH"
    return "NEUTRAL"


def _confidence(pros, cons):
    """A 0-100 reading derived from how one-sided the analysis is.

    This is a presentation aid, not a model output: the LLM is never asked for a
    number, and inventing precision it did not express would be worse than a
    stated heuristic. A balanced or unparsed analysis sits at 50.
    """
    total = len(pros) + len(cons)
    if total == 0:
        return 50
    return int(round(50 + (len(pros) - len(cons)) / total * 40))


# ── Quotes ────────────────────────────────────────────────────────────────────

def _history_rows(ticker_obj, period, interval):
    try:
        frame = ticker_obj.history(period=period, interval=interval)
    except Exception as e:
        logger.warning("History lookup failed: %s", type(e).__name__)
        return []

    if frame is None or frame.empty:
        return []

    rows = []
    for stamp, row in frame.iterrows():
        try:
            rows.append(
                {
                    "timestamp": stamp.to_pydatetime().astimezone(timezone.utc).isoformat(),
                    "timeLabel": stamp.strftime("%H:%M") if interval.endswith("m") else stamp.strftime("%b %d"),
                    "open": round(float(row["Open"]), 2),
                    "high": round(float(row["High"]), 2),
                    "low": round(float(row["Low"]), 2),
                    "close": round(float(row["Close"]), 2),
                    "volume": int(row["Volume"] or 0),
                }
            )
        except (KeyError, TypeError, ValueError):
            # A single malformed bar is skipped; the rest of the series stands.
            continue

    return rows


def quote(symbol):
    """Fetch the fields a signal card needs, or None if the symbol will not resolve."""
    yf = _yfinance()
    if yf is None:
        return None

    try:
        ticker_obj = yf.Ticker(symbol)
        info = ticker_obj.info or {}
    except Exception as e:
        logger.warning("Quote lookup failed for %s: %s", symbol, type(e).__name__)
        return None

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    previous_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
    if price is None:
        return None

    volume = info.get("volume") or info.get("regularMarketVolume") or 0
    average_volume = info.get("averageVolume") or 0
    change = (price - previous_close) if previous_close else 0.0

    return {
        "symbol": symbol,
        "companyName": info.get("longName") or info.get("shortName") or symbol,
        "sector": info.get("sector") or "Unknown",
        "industry": info.get("industry") or "Unknown",
        "marketCap": int(info.get("marketCap") or 0),
        "price": round(float(price), 2),
        "change": round(float(change), 2),
        "changePercent": round(float(change / previous_close * 100), 2) if previous_close else 0.0,
        "volume": int(volume),
        "averageVolume": int(average_volume),
        # Guarded: a brand-new listing reports averageVolume 0, and dividing by
        # it would take down the whole signals response.
        "volumeSurgeRatio": round(volume / average_volume, 2) if average_volume else 0.0,
        "history": _history_rows(ticker_obj, period="1d", interval="5m"),
    }


def index_quote(symbol):
    """Last price and percent change for an index, or None."""
    yf = _yfinance()
    if yf is None:
        return None

    try:
        frame = yf.Ticker(symbol).history(period="2d", interval="1d")
    except Exception as e:
        logger.warning("Index lookup failed for %s: %s", symbol, type(e).__name__)
        return None

    if frame is None or frame.empty:
        return None

    closes = [float(value) for value in frame["Close"].tolist()]
    last = closes[-1]
    previous = closes[-2] if len(closes) > 1 else last
    change_percent = ((last - previous) / previous * 100) if previous else 0.0

    return {"price": round(last, 2), "changePercent": round(change_percent, 2)}


def futures_contract(symbol, name, category):
    """Build one FuturesContract row, or None if the symbol will not resolve."""
    yf = _yfinance()
    if yf is None:
        return None

    try:
        frame = yf.Ticker(symbol).history(period="3mo", interval="1d")
    except Exception as e:
        logger.warning("Futures lookup failed for %s: %s", symbol, type(e).__name__)
        return None

    if frame is None or frame.empty:
        return None

    closes = [float(value) for value in frame["Close"].tolist()]
    last = closes[-1]
    previous = closes[-2] if len(closes) > 1 else last
    rsi = compute_rsi(closes)

    return {
        "symbol": symbol,
        "name": name,
        "category": category,
        "lastPrice": round(last, 2),
        "change": round(last - previous, 2),
        "changePercent": round((last - previous) / previous * 100, 2) if previous else 0.0,
        # The UI types rsi14 as a number, so an unmeasurable RSI is reported as
        # the neutral midpoint here and the trend is independently SIDEWAYS.
        "rsi14": rsi if rsi is not None else 50.0,
        "trend": _trend(last, previous, rsi),
    }


def market_status(now=None):
    """Classify the US session from the current UTC time.

    Weekday regular hours are 14:30-21:00 UTC. This deliberately does not know
    about market holidays: a holiday reads as CLOSED only if it falls on a
    weekend, so the label is a convenience, not a trading calendar.
    """
    now = now or datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return "CLOSED"

    minutes = now.hour * 60 + now.minute
    if 8 * 60 <= minutes < 14 * 60 + 30:
        return "PRE_MARKET"
    if 14 * 60 + 30 <= minutes < 21 * 60:
        return "REGULAR_HOURS"
    if 21 * 60 <= minutes < 24 * 60:
        return "POST_MARKET"
    return "CLOSED"
