import unittest
from unittest.mock import patch, MagicMock
from utils.intelligence_client import IntelligenceClient

class TestIntelligenceClient(unittest.TestCase):
    def setUp(self):
        self.client = IntelligenceClient()

    @patch('yfinance.Ticker')
    def test_get_market_sentiment(self, mock_ticker):
        # Mock yfinance Ticker and news
        mock_stock = MagicMock()
        mock_stock.news = [{'title': 'Market Rally'}, {'title': 'Breaking News'}]
        mock_ticker.return_value = mock_stock

        sentiment = self.client.get_market_sentiment("AAPL")
        
        self.assertEqual(len(sentiment['headlines']), 2)
        self.assertIn('Market Rally', sentiment['headlines'])
        self.assertIn('Breaking News', sentiment['headlines'])

if __name__ == '__main__':
    unittest.main()
