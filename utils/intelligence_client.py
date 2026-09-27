import yfinance as yf
import logging

logger = logging.getLogger(__name__)

class IntelligenceClient:
    def __init__(self):
        # We can configure this client as needed
        pass

    def get_market_sentiment(self, ticker):
        """
        Fetches recent news/sentiment headlines for a ticker using yfinance.
        """
        try:
            stock = yf.Ticker(ticker)
            news = stock.news
            
            # Extract and format headlines for LLM input
            headlines = [item['title'] for item in news[:5]] # Get top 5 news items
            
            return {
                "ticker": ticker,
                "headlines": headlines,
                "summary": " | ".join(headlines)
            }
        except Exception as e:
            logger.error(f"Failed to fetch sentiment for {ticker}: {e}")
            return {"ticker": ticker, "headlines": [], "summary": "No sentiment data available."}
