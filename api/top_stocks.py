"""Well-known tickers for the home page: price and daily change, grouped by market.

Each quote reuses market_source.index_quote, so Yahoo is asked at most once a minute
per symbol however often the dashboard polls.
"""

from . import market_source

# shortcut: a fixed list of famous tickers, not a full-market view; edit here to change what the home page shows.
TOP_STOCK_GROUPS = [
    ("Tech stocks", ["NVDA", "AMD", "AAPL", "MSFT"]),
    ("Market ETFs", ["SPY", "QQQ", "IWM"]),
    ("Crypto", ["BTC-USD", "ETH-USD", "IBIT"]),
    ("Energy and gold", ["XOM", "GLD"]),
]


def top_stock_groups():
    groups = []
    for name, symbols in TOP_STOCK_GROUPS:
        items = []
        for symbol in symbols:
            quote = market_source.index_quote(symbol)
            items.append({
                "symbol": symbol,
                # None means the lookup failed; the page shows "unavailable" for that tile.
                "price": quote["price"] if quote else None,
                "changePercent": quote["changePercent"] if quote else None,
            })
        groups.append({"name": name, "items": items})
    return groups
