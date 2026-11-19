"""The editable stock universe, grouped by domain (P3). The poll, strategies, scores and dashboard all use it.

Seeded with the owner's table of 16 domains (spec 2026-11-17, "Decided by the owner after the builder check"); the
spec says 77 tickers but the table lists 75, which is what is seeded. Stored in the SQLite file; on other backends
the seed is used as is.
"""
import logging
import sqlite3
import threading

from config import Config

logger = logging.getLogger(__name__)

MAX_TICKERS = 100  # one Yahoo download per poll covers them
SEED = {
    "Neocloud": ["CRWV", "NBIS", "IREN", "APLD"],
    "Hyperscalers": ["MSFT", "AMZN", "GOOGL", "META", "ORCL"],
    "AI chips and semiconductors": ["NVDA", "AMD", "AVGO", "TSM", "ARM", "INTC", "MU", "QCOM", "ASML", "AMAT", "LRCX"],
    "AI hardware and networking": ["AAPL", "CSCO", "DELL", "SMCI", "ANET"],
    "Software and data": ["CRM", "ADBE", "NOW", "PLTR", "SNOW"],
    "Cybersecurity": ["CRWD", "PANW"],
    "Internet and media": ["NFLX", "UBER", "DIS", "SPOT"],
    "Power for AI": ["VST", "CEG", "NEE"],
    "Oil and gas": ["XOM", "CVX", "COP", "OXY", "SLB"],
    "Banks": ["JPM", "BAC", "GS", "MS"],
    "Payments": ["V", "MA", "PYPL"],
    "Retail and staples": ["WMT", "COST", "HD", "LOW", "TGT", "PG", "KO"],
    "Consumer brands": ["TSLA", "NKE", "SBUX", "MCD"],
    "Healthcare and pharma": ["LLY", "UNH", "JNJ", "ABBV", "PFE"],
    "Industrials and defense": ["CAT", "GE", "BA", "LMT", "RTX"],
    "Market ETFs": ["SPY", "QQQ", "IWM"],
}
SEED_TICKERS = [t for tickers in SEED.values() for t in tickers]
# Yahoo's exchange codes to TradingView's prefixes.
EXCHANGES = {"NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NAS": "NASDAQ", "NYQ": "NYSE", "NYS": "NYSE",
             "PCX": "AMEX", "ASE": "AMEX"}


class UniverseStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        with self._lock, self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS universe (ticker TEXT PRIMARY KEY, domain TEXT NOT NULL, "
                         "exchange TEXT, position INTEGER NOT NULL)")
            if conn.execute("SELECT COUNT(*) FROM universe").fetchone()[0] == 0:
                conn.executemany("INSERT INTO universe (ticker, domain, position) VALUES (?, ?, ?)",
                                 [(t, d, i) for i, (t, d) in enumerate((t, d) for d, ts in SEED.items() for t in ts)])

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def items(self) -> list:
        with self._connect() as conn:
            rows = conn.execute("SELECT ticker, domain, exchange FROM universe ORDER BY position").fetchall()
        return [{"ticker": t, "domain": d, "exchange": e} for t, d, e in rows]

    def replace(self, items) -> list:
        """Store `items` ([{"ticker", "domain"}], already validated) in order; known exchanges are kept."""
        with self._lock, self._connect() as conn:
            known = dict(conn.execute("SELECT ticker, exchange FROM universe").fetchall())
            conn.execute("DELETE FROM universe")
            conn.executemany("INSERT INTO universe (ticker, domain, exchange, position) VALUES (?, ?, ?, ?)",
                             [(x["ticker"], x["domain"], known.get(x["ticker"]), i) for i, x in enumerate(items)])
        return self.items()

    def set_exchange(self, ticker, exchange):
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE universe SET exchange = ? WHERE ticker = ?", (exchange, ticker))


def universe_store():
    return UniverseStore(Config.SQLITE_PATH) if Config.STORAGE_BACKEND == "sqlite" else None


def universe_tickers() -> list:
    store = universe_store()
    return [x["ticker"] for x in store.items()] if store else list(SEED_TICKERS)


def lookup_exchange(ticker):
    """TradingView's exchange prefix from Yahoo's quote data, or None when unknown or unavailable."""
    import yfinance as yf

    try:
        return EXCHANGES.get(yf.Ticker(ticker).fast_info["exchange"])
    except Exception as e:  # network, rate limit, delisted
        logger.warning("Exchange lookup failed for %s: %s", ticker, e)
        return None


def fill_exchanges(store, lookup=None, limit=20) -> int:
    """Look up the exchange of up to `limit` tickers that have none; returns how many were found."""
    lookup, found = lookup or lookup_exchange, 0
    for x in [x for x in store.items() if not x["exchange"]][:limit]:
        exchange = lookup(x["ticker"])
        if exchange:
            store.set_exchange(x["ticker"], exchange)
            found += 1
    return found
