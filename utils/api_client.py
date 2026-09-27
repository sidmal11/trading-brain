import requests
import logging
from config import Config

logger = logging.getLogger(__name__)

class ApiClient:
    def __init__(self):
        self.base_url = Config.STOCK_SCREENER_URL
        self.headers = {"Authorization": f"Bearer {Config.STOCK_SCREENER_API_KEY}"}

    def get_market_scan_results(self, market="US"):
        """Fetches scan results (e.g., breakouts)."""
        # This is a hypothetical endpoint based on the architecture. 
        # In a real scenario, we might need to inspect the API docs 
        # more closely or use the /scan or /market-scan endpoints.
        url = f"{self.base_url}/api/v1/market-scan"
        params = {"market": market}
        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch market scan: {e}")
            return []

    def get_stock_details(self, ticker):
        """Fetches detailed stock data."""
        url = f"{self.base_url}/api/v1/stocks/{ticker}"
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch stock details for {ticker}: {e}")
            return {}
