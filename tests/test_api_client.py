import unittest
from unittest.mock import patch, MagicMock
from utils.api_client import ApiClient

class TestApiClient(unittest.TestCase):
    def setUp(self):
        self.client = ApiClient()

    @patch('requests.get')
    def test_get_market_scan_results(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = [{"ticker": "AAPL", "score": 90}]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        results = self.client.get_market_scan_results()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["ticker"], "AAPL")

if __name__ == '__main__':
    unittest.main()
