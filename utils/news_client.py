"""Latest AInvest news-wire headlines per ticker.

Docs: https://docs.ainvest.com/reference/news/wires
The dashboard polls /api/v1/signals every 30s; the wire is only re-read once the
cache is older than NEWS_REFRESH_SECONDS, so the free tier's per-minute limit
is not spent on every poll.
"""

import logging
import time
from datetime import datetime, timezone

import requests

from config import Config

logger = logging.getLogger(__name__)

WIRE_URL = "https://openapi.ainvest.com/open/news/v1/wire/page/history"
MAX_PER_TICKER = 3

# shortcut: one in-process cache keyed by the ticker set; on Lambda each cold
# start refetches. Move to a shared store if that ever costs rate limit.
_cache = {"key": None, "at": 0.0, "news": {}}


def _parse(payload, tickers):
    """Group wire items by the tickers they are tagged with, newest first."""
    wanted = set(tickers)
    items = ((payload or {}).get("data") or {}).get("data") or []
    news = {}
    for item in items:
        title = item.get("title")
        published = item.get("publish_time")
        if not title or not isinstance(published, (int, float)):
            continue
        row = {
            "id": str(item.get("news_id") or item.get("content_id") or title),
            "title": title,
            "source": item.get("source") or "AInvest",
            "publishedAt": datetime.fromtimestamp(published / 1000, tz=timezone.utc).isoformat(),
        }
        for tag in item.get("tag_list") or []:
            code = (tag.get("code") or "").upper()
            if code in wanted and len(news.setdefault(code, [])) < MAX_PER_TICKER:
                news[code].append(row)
    return news


def latest_by_ticker(tickers):
    """Return {ticker: [headline, ...]}. Empty when unconfigured or on failure."""
    tickers = sorted({t.upper() for t in tickers if t})
    if not Config.AINVEST_API_KEY or not tickers:
        return {}

    key = ",".join(tickers)
    if _cache["key"] == key and time.time() - _cache["at"] < Config.NEWS_REFRESH_SECONDS:
        return _cache["news"]

    try:
        response = requests.get(
            WIRE_URL,
            params={"tickers": key, "size": 50},
            headers={"Authorization": f"Bearer {Config.AINVEST_API_KEY}"},
            timeout=5,
        )
        response.raise_for_status()
        news = _parse(response.json(), tickers)
    except (requests.RequestException, ValueError) as e:
        # Serve the last good headlines rather than blanking the cards.
        logger.warning("AInvest news read failed: %s", type(e).__name__)
        news = _cache["news"] if _cache["key"] == key else {}

    _cache.update(key=key, at=time.time(), news=news)
    return news
