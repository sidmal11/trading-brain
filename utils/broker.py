"""Mock broker. No real broker is ever contacted; any other BROKER value is refused."""

from typing import Callable, Optional

from config import Config


def whole_shares(dollars: float, price: Optional[float]) -> int:
    if price is None or price <= 0:
        return 0
    return int(dollars // price)


class BrokerError(Exception):
    pass


class MockBroker:
    name = "mock"

    def __init__(self, price_of: Callable[[str], Optional[float]]):
        self._price_of = price_of

    def _fill(self, ticker: str) -> float:
        price = self._price_of(ticker)
        if price is None:
            raise BrokerError(f"No price for {ticker}.")
        return price

    def buy(self, ticker: str, quantity: int, order_id: int) -> float:
        return self._fill(ticker)

    def sell(self, ticker: str, quantity: int, order_id: int) -> float:
        return self._fill(ticker)


def get_broker(price_of: Callable[[str], Optional[float]]) -> MockBroker:
    if Config.BROKER != "mock":
        raise ValueError(f"Unsupported BROKER '{Config.BROKER}': only 'mock' is allowed.")
    return MockBroker(price_of)
