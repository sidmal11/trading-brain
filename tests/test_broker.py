import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Config
from utils.broker import BrokerError, MockBroker, whole_shares


def test_whole_shares_rounds_down():
    assert whole_shares(500, 182.4) == 2
    assert whole_shares(500, 812.3) == 0
    assert whole_shares(500, None) == 0
    assert whole_shares(500, 0) == 0


def test_mock_broker_fills_at_price_of():
    broker = MockBroker(lambda t: 182.4)
    assert broker.buy("AAPL", 2, 1) == 182.4
    assert broker.sell("AAPL", 2, 1) == 182.4


def test_mock_broker_refuses_without_price():
    broker = MockBroker(lambda t: None)
    with pytest.raises(BrokerError):
        broker.buy("AAPL", 2, 1)
    with pytest.raises(BrokerError):
        broker.sell("AAPL", 2, 1)


def test_unknown_broker_refuses_to_start(monkeypatch):
    from api.main import create_app

    monkeypatch.setattr(Config, "BROKER", "robinhood")
    with pytest.raises(Exception, match="robinhood"):
        create_app()
