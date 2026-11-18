"""Approval buys, Close sells and the positions read, all in the mock account.

A buy is claimed (a PENDING row) before any check, so every refusal is recorded as a
FAILED order with its reason. Once claimed, every path ends in fill or fail: a PENDING
row left behind would block that alert for good.
"""

from config import Config
from utils.broker import BrokerError, MockBroker, get_broker
from utils.market_clock import session_fraction, trading_date
from utils.settings_store import effective_settings, settings_store
from utils.trades_store import TradesStore

from . import market_source
from .validity import invalid_reason, quantity_for, suggest_quantity

BROKER_ERROR = "Broker error."


class TradeRefused(Exception):
    def __init__(self, reason, order=None):
        super().__init__(reason)
        self.reason = reason
        self.order = order


class DecisionNotRecorded(Exception):
    """The alert store did not take the status change."""


def live_price(snapshot):
    """The latest price: Yahoo's pre/post-market price when the regular session is closed and it has one."""
    if session_fraction() is None and snapshot.get("extendedPrice"):
        return snapshot["extendedPrice"]
    return snapshot["price"]


ALREADY_REJECTED = "Already rejected."
ALREADY_BOUGHT = "Already bought. Close the position instead."
INFO_ONLY = "Information only: this strategy does not send buy requests."


def price_of(ticker):
    snapshot = market_source.quote(ticker)
    return live_price(snapshot) if snapshot else None


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
    ticker = alert["ticker"].upper()
    if any(p["ticker"].upper() == ticker for p in store.open_positions()):
        return f"Already holding {ticker}."
    if store.filled_buys_on(today) >= settings["maxOrdersPerDay"]:
        return f"Daily order limit reached ({settings['maxOrdersPerDay']})."
    if not price or price <= 0:
        return "Price unavailable."
    if round(quantity * price, 2) < 1:  # cents: $1 at an awkward price must not fall a hair short
        return "Minimum order is $1."
    if store.cash(Config.MOCK_STARTING_CASH) < quantity * price:
        return "Not enough mock cash."
    return None


def _num(value):
    return None if value is None else float(value)  # DynamoDB hands back Decimal


def execute_approval(alert, db, shares=None, dollars=None):
    """Buy for an approved alert: `shares`, `dollars`, or neither (the suggested quantity).

    Returns the order, or None when trading is off.
    """
    alert_id, ticker = alert["alertId"], alert["ticker"]
    if alert.get("status") == "EXPIRED":  # before the trading switch: never status-flip an expired alert
        raise TradeRefused(f"Request expired: {alert.get('expiredReason') or 'No longer valid.'}")
    if alert.get("status") == "REJECTED":  # a leftover button or another admin's copy must not undo a rejection
        raise TradeRefused(ALREADY_REJECTED)
    if alert.get("status") == "INFO":
        raise TradeRefused(INFO_ONLY)
    settings = effective_settings()
    store = get_trades_store() if settings_store() is not None else None  # the trades file lives only on SQLite
    existing = store.live_buy(alert_id) if store else None
    if existing:
        # Already bought (or being bought): a repeat approval answers with that order, whatever the price did since.
        if existing["status"] == "FILLED":
            _set_approved(db, alert_id)
        return existing

    price = price_of(ticker)  # before the claim: no slow work between claim and fill/fail
    stop = _num(alert.get("stopPrice"))
    reason = invalid_reason({**alert, "stopPrice": stop, "priceAtAlert": _num(alert.get("priceAtAlert"))},
                            price, settings)
    if reason:
        db.expire_alert(alert_id, reason)
        raise TradeRefused(f"Request expired: {reason}")
    if store is None or not settings["tradingEnabled"]:
        _set_approved(db, alert_id)
        return None

    priced = price is not None and price > 0  # never size against a missing or zero price
    quantity = quantity_for(price, shares, dollars, suggest_quantity(price, stop, settings)) if priced else 0
    today = trading_date()
    order, created = store.claim_buy(alert_id, ticker, quantity, today, strategy=alert.get("strategy"),
                                     stop_price=stop, target_price=_num(alert.get("targetPrice")))
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


def reject(alert, db):
    """Record a rejection, unless the alert already has a buy (its position is closed with Close, not relabelled)."""
    if alert.get("status") == "INFO":
        raise TradeRefused(INFO_ONLY)
    store = get_trades_store() if settings_store() is not None else None
    if store and store.live_buy(alert["alertId"]):
        raise TradeRefused(ALREADY_BOUGHT)
    if not db.update_alert_status(alert["alertId"], "REJECTED"):
        raise DecisionNotRecorded(alert["alertId"])
    return True


def close_position(ticker):
    """Sell the whole position, at any hour. The kill switch does not block Close."""
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
