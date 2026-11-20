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

import copy
import logging
import re
import time
from datetime import datetime, timezone

from utils.market_clock import session_fraction
from indicators import rsi

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
        return None
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
    """Wilder's RSI of the latest close (indicators.rsi), rounded to 2 decimals; None when it cannot be measured.

    None, not a neutral 50, for a thin series or one with a missing close (Yahoo can return NaN for today):
    50 is a real reading that means balance, and inventing it would claim a momentum never measured.
    """
    if not closes or len(closes) <= period:
        return None
    try:
        return round(rsi(closes, period)[-1], 2)
    except ValueError:
        return None


# ── Sentiment parsing ─────────────────────────────────────────────────────────

_BULLET = re.compile(r"^\s*(?:[-*•–]|\d+[.)])\s+")
# A heading is a word that looks like one: bold anywhere ("**Pros:**"), or at the start of a line on its own or
# followed by a colon ("Cons:", "## Bullish"). A bare word inside a sentence ("No bullish MACD cross") is not one.
_HEADING = re.compile(
    r"\*\*\s*(pros|cons|bullish|bearish)\s*:?\s*\*\*\s*:?"
    r"|^[ \t#>*_]*(pros|cons|bullish|bearish)[ \t*_]*(?::|$)",
    re.IGNORECASE | re.MULTILINE,
)
_INLINE_BULLET = re.compile(r"\s+[-•–]\s+(?=\*\*)")  # "... - **Next point:** ..." on one line
_VERDICT = re.compile(r"^(overall|in summary|summary|bottom line|verdict|conclusion|in short|net)\b", re.IGNORECASE)


def _clean(text):
    """Plain text: no markdown bold/italic markers or heading hashes, single spaces."""
    text = re.sub(r"\*\*|__|^#+\s*", "", text.strip())
    text = re.sub(r"^[*_]+|[*_]+$", "", text.strip())  # an item wrapped in *italics*
    return re.sub(r"\s+", " ", text).strip()


def _items(block):
    """The bullet points of a block, cleaned; empty or punctuation-only fragments are dropped."""
    out = []
    for line in block.splitlines():
        for part in _INLINE_BULLET.split(line):
            item = _clean(_BULLET.sub("", part))
            if item and not re.fullmatch(r"[\W_]*", item):
                out.append(item)
    return out


def split_pros_cons(analysis):
    """Pull pros and cons bullets out of the LLM's write-up, the same way for every card.

    Returns (pros, cons, summary). The text before the first heading and any closing "Overall ..." line go to
    `summary`. Anything without both headings is kept whole (cleaned) in `summary` rather than discarded,
    because an unparsed analysis is still the most useful thing on the card.
    """
    if not analysis:
        return [], [], ""
    heads = [(m.start(), m.end(), (m.group(1) or m.group(2)).lower()) for m in _HEADING.finditer(analysis)]
    pro = next((h for h in heads if h[2] in ("pros", "bullish")), None)
    con = next((h for h in heads if h[2] in ("cons", "bearish")), None)
    if pro is None or con is None:
        return [], [], _clean(analysis)

    first, second = sorted([pro, con])
    blocks = {first[2]: analysis[first[1]:second[0]], second[2]: analysis[second[1]:]}
    pros = _items(blocks[pro[2]])
    cons = _items(blocks[con[2]])
    summary = [_clean(analysis[:first[0]])]
    # A closing verdict is neither a pro nor a con: it moves to the summary (it is usually the last item).
    for side in (pros, cons):
        while side and _VERDICT.match(side[-1]):
            summary.append(side.pop())
    return pros, cons, " ".join(x for x in summary if x)


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


_CACHE_TTL_SECONDS = 60
# shortcut: keeps every symbol it has seen; fine for a few hundred tickers, add a size cap if the universe grows.
_cache = {}


def _cached(key, fetch):
    """Reuse a successful read for a minute, so each 30-second dashboard poll does not hit Yahoo again.

    Failures (None) are not stored, so the next poll retries them.
    """
    now = time.time()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return copy.deepcopy(hit[1])
    value = fetch()
    if value is not None:
        _cache[key] = (now + _CACHE_TTL_SECONDS, copy.deepcopy(value))
    return value


def quote(symbol):
    return _cached(("quote", symbol), lambda: _fetch_quote(symbol))


def index_quote(symbol):
    return _cached(("index", symbol), lambda: _fetch_index(symbol))


def _fetch_quote(symbol):
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
        "marketCap": int(info["marketCap"]) if info.get("marketCap") else None,
        "price": round(float(price), 2),
        "change": round(float(change), 2),
        "changePercent": round(float(change / previous_close * 100), 2) if previous_close else 0.0,
        "volume": int(volume),
        "averageVolume": int(average_volume),
        # Guarded: a brand-new listing reports averageVolume 0, and dividing by
        # it would take down the whole signals response.
        "volumeSurgeRatio": round(volume / average_volume, 2) if average_volume else 0.0,
        "history": _history_rows(ticker_obj, period="1d", interval="5m"),
        "extendedPrice": _extended_price(info),
    }


def _extended_price(info):
    """The newer of Yahoo's pre- and post-market prices while the regular session is closed, else None.

    Only a price stamped after the regular close counts: after 16:00 Yahoo can still report this morning's pre-market.
    """
    if session_fraction() is not None:
        return None
    close = info.get("regularMarketTime") or 0
    quotes = [(info.get(f"{side}MarketTime") or 0, info[f"{side}MarketPrice"])
              for side in ("pre", "post") if info.get(f"{side}MarketPrice")]
    quotes = [q for q in quotes if q[0] > close]
    return round(float(max(quotes)[1]), 2) if quotes else None


def _fetch_index(symbol):
    """Last price and percent change for an index, or None."""
    yf = _yfinance()
    if yf is None:
        return None

    try:
        frame = yf.Ticker(symbol).history(period="5d", interval="1d")
    except Exception as e:
        logger.warning("Index lookup failed for %s: %s", symbol, type(e).__name__)
        return None

    if frame is None or frame.empty:
        return None

    # Yahoo adds a placeholder row for today with no close (NaN) until the session prints;
    # NaN is not valid JSON, so keep only real closes.
    closes = [float(value) for value in frame["Close"].dropna().tolist()]
    if not closes:
        return None
    last = closes[-1]
    previous = closes[-2] if len(closes) > 1 else last
    change_percent = ((last - previous) / previous * 100) if previous else 0.0

    return {"price": round(last, 2), "changePercent": round(change_percent, 2)}


def futures_contract(symbol, name, category):
    """One FuturesContract row, reused for a minute like quotes, or None if the symbol will not resolve."""
    return _cached(("futures", symbol), lambda: _futures_contract(symbol, name, category))


def _futures_contract(symbol, name, category):
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
