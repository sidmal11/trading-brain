import logging

import yfinance as yf

logger = logging.getLogger(__name__)

# shortcut: fixed list of large US tickers, not a full-market scan; widen it when the screener needs tickers outside this list
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "NFLX", "AVGO",
    "JPM", "BAC", "GS", "V", "MA", "XOM", "CVX", "WMT", "COST", "HD",
    "DIS", "NKE", "SBUX", "INTC", "CSCO", "ORCL", "CRM", "ADBE", "PYPL", "UBER",
]


def _volume_score(volumes, session_fraction=1.0):
    """Latest volume, projected to a full day by dividing by session_fraction, over the average of the prior days; None if too little history."""
    volumes = [v for v in volumes if v == v and v > 0]  # drops NaN and zero-volume days
    if len(volumes) < 6:
        return None
    baseline = sum(volumes[:-1]) / (len(volumes) - 1)
    return round(volumes[-1] / session_fraction / baseline, 2)


class ApiClient:
    """Market data from Yahoo Finance (via yfinance). Unofficial and rate-limited by Yahoo."""

    def get_market_scan_results(self, market="US", session_fraction=1.0, trading_date=None):
        try:
            data = yf.download(UNIVERSE, period="1mo", interval="1d", group_by="ticker",
                               threads=False, progress=False, auto_adjust=False)
        except Exception as e:
            logger.error(f"Yahoo download failed: {e}")
            return []

        results = []
        for ticker in UNIVERSE:
            try:
                bars = data[ticker].dropna(subset=["Volume"])
            except KeyError:
                continue
            # Yahoo may not have posted today's bar yet; the newest bar would then be a full prior day, and projecting it invents a surge
            if trading_date and (bars.empty or bars.index[-1].strftime("%Y-%m-%d") != trading_date):
                continue
            score = _volume_score(list(bars["Volume"]), session_fraction)
            if score is not None:
                close = bars["Close"].iloc[-1]
                results.append({"ticker": ticker, "volume_score": score, "price": None if close != close else float(close)})
        return sorted(results, key=lambda r: r["volume_score"], reverse=True)

    def get_stock_details(self, ticker):
        try:
            info = yf.Ticker(ticker).info
        except Exception as e:
            logger.error(f"Failed to fetch stock details for {ticker}: {e}")
            return {}
        keys = ("shortName", "sector", "industry", "currentPrice", "marketCap", "trailingPE", "fiftyTwoWeekHigh", "fiftyTwoWeekLow")
        return {k: info.get(k) for k in keys if info.get(k) is not None}
