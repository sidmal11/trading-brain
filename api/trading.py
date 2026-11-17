"""Approval buys, Close sells and the positions read, all in the mock account.

A buy is claimed (a PENDING row) before any check, so every refusal is recorded as a
FAILED order with its reason. Once claimed, every path ends in fill or fail: a PENDING
row left behind would block that alert for good.
"""

from config import Config
from utils.broker import BrokerError, MockBroker, get_broker, whole_shares
from utils.market_clock import session_fraction, trading_date
from utils.settings_store import effective_settings, settings_store
from utils.trades_store import TradesStore

from . import market_source

BROKER_ERROR = "Broker error."


class TradeRefused(Exception):
    def __init__(self, reason, order=None):
        super().__init__(reason)
        self.reason = reason
        self.order = order


class DecisionNotRecorded(Exception):
    """The alert store did not take the status change."""


def price_of(ticker):
    snapshot = market_source.quote(ticker)
    return snapshot["price"] if snapshot else None


def get_trades_store():
    return TradesStore(Config.TRADES_SQLITE_PATH)


def _set_approved(db, alert_id):
    if not db.update_alert_status(alert_id, "APPROVED"):
        raise DecisionNotRecorded(alert_id)


def _settle(store, order, place, price):
    """Run the broker call and fill; any failure marks the order FAILED."""
    try:
        return store.fill(order["id"], place(get_broker(lambda _ticker: price)))
    except BrokerError:
        raise TradeRefused(BROKER_ERROR, store.fail(order["id"], BROKER_ERROR))
    except Exception:
        store.fail(order["id"], BROKER_ERROR)
        raise


def _buy_refusal(alert, today, store, settings, price, quantity):
    if session_fraction() is None:
        return "Market is closed."
    if alert.get("tradingDate") != today:
        return "Alert expired: it is not from today."
    if store.filled_buys_on(today) >= settings["maxOrdersPerDay"]:
        return f"Daily order limit reached ({settings['maxOrdersPerDay']})."
    if price is None:
        return "Price unavailable."
    if quantity <= 0:
        return f"${settings['dollarsPerTrade']} buys no whole share at ${price:.2f}."
    if store.cash(Config.MOCK_STARTING_CASH) < quantity * price:
        return "Not enough mock cash."
    return None


def _num(value):
    return None if value is None else float(value)  # DynamoDB hands back Decimal


def execute_approval(alert, db):
    """Buy for an approved alert. Returns the order, or None when trading is off."""
    alert_id, ticker = alert["alertId"], alert["ticker"]
    settings = effective_settings()
    if settings_store() is None or not settings["tradingEnabled"]:
        _set_approved(db, alert_id)
        return None

    price = price_of(ticker)  # before the claim: no slow work between claim and fill/fail
    quantity = whole_shares(settings["dollarsPerTrade"], price)
    today = trading_date()
    store = get_trades_store()
    order, created = store.claim_buy(alert_id, ticker, quantity, today, strategy=alert.get("strategy"),
                                     stop_price=_num(alert.get("stopPrice")), target_price=_num(alert.get("targetPrice")))
    if not created:
        # Another approval owns this order; never fill or fail it from here.
        if order["status"] == "FILLED":
            _set_approved(db, alert_id)
        return order

    try:
        reason = _buy_refusal(alert, today, store, settings, price, quantity)
    except Exception:
        store.fail(order["id"], BROKER_ERROR)
        raise
    if reason:
        raise TradeRefused(reason, store.fail(order["id"], reason))

    order = _settle(store, order, lambda broker: broker.buy(ticker, quantity, order["id"]), price)
    _set_approved(db, alert_id)
    return order


def close_position(ticker):
    """Sell the whole position. The kill switch does not block Close."""
    if session_fraction() is None:
        raise TradeRefused("Market is closed.")
    price = price_of(ticker)  # before the claim: no slow work between claim and fill/fail
    if price is None:
        raise TradeRefused("Price unavailable.")
    store = get_trades_store()
    # The position is read inside the claim, so a second Close cannot sell it again.
    order = store.claim_sell(ticker, trading_date())
    if order is None:
        raise LookupError(ticker)
    return _settle(store, order, lambda broker: broker.sell(ticker, order["quantity"], order["id"]), price)


def positions_snapshot():
    store = get_trades_store()
    positions = []
    for p in store.open_positions():
        price = price_of(p["ticker"])
        cost = p["costBasis"]
        gain = None if price is None else price * p["quantity"] - cost
        positions.append({
            "ticker": p["ticker"],
            "quantity": p["quantity"],
            "averageCost": p["averageCost"],
            "currentPrice": price,
            "marketValue": None if price is None else round(price * p["quantity"], 2),
            "unrealizedPnl": None if gain is None else round(gain, 2),
            "unrealizedPnlPercent": None if gain is None else round(gain / cost * 100, 2),
            "stopPrice": p["stopPrice"],
            "targetPrice": p["targetPrice"],
        })
    return {
        "broker": MockBroker.name,
        "startingCash": Config.MOCK_STARTING_CASH,
        "cash": store.cash(Config.MOCK_STARTING_CASH),
        "positions": positions,
    }
