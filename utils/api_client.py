import logging

import yfinance as yf

logger = logging.getLogger(__name__)

# shortcut: fixed list of large US tickers, not a full-market scan; widen it when the screener needs tickers outside this list
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "NFLX", "AVGO",
    "JPM", "BAC", "GS", "V", "MA", "XOM", "CVX", "WMT", "COST", "HD",
    "DIS", "NKE", "SBUX", "INTC", "CSCO", "ORCL", "CRM", "ADBE", "PYPL", "UBER",
]


def _volume_score(volumes):
    """Latest volume divided by the average of the prior days, or None if there is too little history."""
    volumes = [v for v in volumes if v == v and v > 0]  # drops NaN and zero-volume days
    if len(volumes) < 6:
        return None
    baseline = sum(volumes[:-1]) / (len(volumes) - 1)
    return round(volumes[-1] / baseline, 2)


class ApiClient:
    """Market data from Yahoo Finance (via yfinance). Unofficial and rate-limited by Yahoo."""

    def get_market_scan_results(self, market="US"):
        try:
            data = yf.download(UNIVERSE, period="1mo", interval="1d", group_by="ticker",
                               threads=False, progress=False, auto_adjust=False)
        except Exception as e:
            logger.error(f"Yahoo download failed: {e}")
            return []

        results = []
        for ticker in UNIVERSE:
            try:
                volumes = list(data[ticker]["Volume"].dropna())
            except KeyError:
                continue
            score = _volume_score(volumes)
            if score is not None:
                results.append({"ticker": ticker, "volume_score": score})
        return sorted(results, key=lambda r: r["volume_score"], reverse=True)

    def get_stock_details(self, ticker):
        try:
            info = yf.Ticker(ticker).info
        except Exception as e:
            logger.error(f"Failed to fetch stock details for {ticker}: {e}")
            return {}
        keys = ("shortName", "sector", "industry", "currentPrice", "marketCap", "trailingPE", "fiftyTwoWeekHigh", "fiftyTwoWeekLow")
        return {k: info.get(k) for k in keys if info.get(k) is not None}
