import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils import api_client  # noqa: E402
from utils.api_client import ApiClient, _volume_score  # noqa: E402


def _fake_download(volumes_by_ticker, closes_by_ticker=None, last_date="2026-10-06"):
    """Mimic yf.download(group_by='ticker'): columns are (ticker, field)."""
    n = len(next(iter(volumes_by_ticker.values())))
    index = pd.date_range(end=last_date, periods=n)
    closes = closes_by_ticker or {}
    cols = {}
    for t, v in volumes_by_ticker.items():
        cols[(t, "Volume")] = list(v)
        cols[(t, "Close")] = list(closes.get(t, [10.0] * n))
    return pd.DataFrame(cols, index=index)


class TestVolumeScore(unittest.TestCase):
    def test_latest_volume_against_prior_average(self):
        self.assertEqual(_volume_score([100, 100, 100, 100, 100, 300]), 3.0)

    def test_too_little_history_gives_none(self):
        self.assertIsNone(_volume_score([100, 100, 300]))

    def test_partial_day_volume_is_projected(self):
        # 150 so far at half the session projects to 300, which is 3x the 100 average
        self.assertEqual(_volume_score([100] * 5 + [150], session_fraction=0.5), 3.0)

    def test_non_positive_session_fraction_gives_none(self):
        self.assertIsNone(_volume_score([100] * 5 + [150], session_fraction=0))
        self.assertIsNone(_volume_score([100] * 5 + [150], session_fraction=-0.5))

    def test_zero_and_nan_days_are_ignored(self):
        self.assertEqual(_volume_score([100, 0, float("nan"), 100, 100, 100, 100, 200]), 2.0)


class TestScanResults(unittest.TestCase):
    def test_ranks_tickers_by_volume_score(self):
        frame = _fake_download({
            "AAPL": [100] * 5 + [150],
            "MSFT": [100] * 5 + [400],
        })
        with patch.object(api_client, "UNIVERSE", ["AAPL", "MSFT"]), \
             patch.object(api_client.yf, "download", return_value=frame):
            results = ApiClient().get_market_scan_results()
        self.assertEqual(results, [
            {"ticker": "MSFT", "volume_score": 4.0, "price": 10.0},
            {"ticker": "AAPL", "volume_score": 1.5, "price": 10.0},
        ])

    def test_results_carry_latest_close_as_price(self):
        frame = _fake_download({"AAPL": [100] * 5 + [300]}, closes_by_ticker={"AAPL": [10, 11, 12, 13, 14, 15.5]})
        with patch.object(api_client, "UNIVERSE", ["AAPL"]),              patch.object(api_client.yf, "download", return_value=frame):
            results = ApiClient().get_market_scan_results()
        self.assertEqual(results, [{"ticker": "AAPL", "volume_score": 3.0, "price": 15.5}])

    def test_nan_close_gives_price_none(self):
        frame = _fake_download({"AAPL": [100] * 5 + [300]}, closes_by_ticker={"AAPL": [10, 11, 12, 13, 14, float("nan")]})
        with patch.object(api_client, "UNIVERSE", ["AAPL"]),              patch.object(api_client.yf, "download", return_value=frame):
            results = ApiClient().get_market_scan_results()
        self.assertIsNone(results[0]["price"])

    def test_ticker_without_todays_bar_is_skipped(self):
        frame = _fake_download({"AAPL": [100] * 5 + [300]}, last_date="2026-11-13")
        with patch.object(api_client, "UNIVERSE", ["AAPL"]),              patch.object(api_client.yf, "download", return_value=frame):
            results = ApiClient().get_market_scan_results(session_fraction=0.5, trading_date="2026-11-16")
        self.assertEqual(results, [])

    def test_todays_bar_present_is_projected(self):
        frame = _fake_download({"AAPL": [100] * 5 + [150]}, last_date="2026-11-16")
        with patch.object(api_client, "UNIVERSE", ["AAPL"]),              patch.object(api_client.yf, "download", return_value=frame):
            results = ApiClient().get_market_scan_results(session_fraction=0.5, trading_date="2026-11-16")
        self.assertEqual(results[0]["volume_score"], 3.0)

    def test_download_failure_returns_empty_list(self):
        with patch.object(api_client.yf, "download", side_effect=RuntimeError("rate limited")):
            self.assertEqual(ApiClient().get_market_scan_results(), [])


class TestStockDetails(unittest.TestCase):
    def test_keeps_only_the_useful_fields(self):
        ticker = MagicMock()
        ticker.info = {"shortName": "Apple", "currentPrice": 190.5, "secretField": "x"}
        with patch.object(api_client.yf, "Ticker", return_value=ticker):
            details = ApiClient().get_stock_details("AAPL")
        self.assertEqual(details, {"shortName": "Apple", "currentPrice": 190.5})


if __name__ == "__main__":
    unittest.main()
