import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils import api_client  # noqa: E402
from utils.api_client import ApiClient, _volume_score  # noqa: E402


def _fake_download(volumes_by_ticker):
    """Mimic yf.download(group_by='ticker'): columns are (ticker, field)."""
    index = pd.date_range("2026-10-01", periods=len(next(iter(volumes_by_ticker.values()))))
    cols = pd.MultiIndex.from_product([list(volumes_by_ticker), ["Volume"]])
    data = [list(v) for v in zip(*volumes_by_ticker.values())]
    return pd.DataFrame(data, index=index, columns=cols)


class TestVolumeScore(unittest.TestCase):
    def test_latest_volume_against_prior_average(self):
        self.assertEqual(_volume_score([100, 100, 100, 100, 100, 300]), 3.0)

    def test_too_little_history_gives_none(self):
        self.assertIsNone(_volume_score([100, 100, 300]))

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
            {"ticker": "MSFT", "volume_score": 4.0},
            {"ticker": "AAPL", "volume_score": 1.5},
        ])

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
