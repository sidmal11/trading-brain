"""Approval buys, Close sells and the positions read, each in the caller's own mock account.

Every function takes the caller's email; the account and the decision are that email's alone. Only the owner's
decisions also set the alert's global status (what Telegram, the cooldown and the expiry sweep read).

A buy is claimed (a PENDING row) before any check, so every refusal is recorded as a
FAILED order with its reason. Once claimed, every path ends in fill or fail: a PENDING
row left behind would block that alert for good.
"""

from config import Config
from utils.broker import BrokerError, MockBroker, get_broker
from utils.decisions_store import decisions_store
from utils.market_clock import session_fraction, trading_date
from utils.settings_store import effective_settings, settings_store
from utils.trades_store import TradesStore

from . import market_source
from .accounts import ACCOUNT_OFF, account_for, is_owner
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


def _on_sqlite():
    return settings_store() is not None  # the trades file and the decisions table live only on SQLite


def decision_of(alert, email):
    """This caller's own decision on the alert, or None. Off SQLite there is one shared status (single operator)."""
    if not _on_sqlite():
        return alert.get("status") if alert.get("status") in ("APPROVED", "REJECTED") else None
    return decisions_store().get(alert["alertId"], email)


def _record(db, alert_id, email, decision):
    """The caller's decision; the owner's (and, off SQLite, anyone's) also becomes the alert's global status."""
    if (not _on_sqlite() or is_owner(email)) and not db.update_alert_status(alert_id, decision):
        raise DecisionNotRecorded(alert_id)
    if _on_sqlite():
        decisions_store().record(alert_id, email, decision)


def _settle(store, order, place, price):
    """Run the broker call and fill; any failure marks the order FAILED."""
    try:
        return store.fill(order["id"], place(get_broker(lambda _ticker: price)))
    except BrokerError:
        raise TradeRefused(BROKER_ERROR, store.fail(order["id"], BROKER_ERROR))
    except Exception:
        store.fail(order["id"], BROKER_ERROR)
        raise


def _buy_refusal(alert, today, store, account_id, settings, price, quantity):
    ticker = alert["ticker"].upper()
    if any(p["ticker"].upper() == ticker for p in store.open_positions(account_id)):
        return f"Already holding {ticker}."
    if store.filled_buys_on(account_id, today) >= settings["maxOrdersPerDay"]:
        return f"Daily order limit reached ({settings['maxOrdersPerDay']})."
    return _cash_refusal(store, account_id, price, quantity)


def _cash_refusal(store, account_id, price, quantity):
    if not price or price <= 0:
        return "Price unavailable."
    if round(quantity * price, 2) < 1:  # cents: $1 at an awkward price must not fall a hair short
        return "Minimum order is $1."
    if store.cash(account_id) < quantity * price:
        return "Not enough mock cash."
    return None


def _num(value):
    return None if value is None else float(value)  # DynamoDB hands back Decimal


def execute_approval(alert, db, email, shares=None, dollars=None):
    """`email` approves the alert. It buys in that email's account (`shares`, `dollars`, or neither for the
    suggested quantity) only when trading, the account and the account's approvals switch are all on.

    Returns the order, or None when the approval only records the decision.
    """
    alert_id, ticker = alert["alertId"], alert["ticker"]
    if alert.get("status") == "EXPIRED":  # before the trading switch: never status-flip an expired alert
        raise TradeRefused(f"Request expired: {alert.get('expiredReason') or 'No longer valid.'}")
    if decision_of(alert, email) == "REJECTED":  # a leftover button or a second copy must not undo this rejection
        raise TradeRefused(ALREADY_REJECTED)
    if alert.get("status") == "INFO":
        raise TradeRefused(INFO_ONLY)
    settings = effective_settings()
    store = get_trades_store() if _on_sqlite() else None
    account = account_for(email) if store else None
    existing = store.live_buy(account["id"], alert_id) if store else None
    if existing:
        # Already bought (or being bought): a repeat approval answers with that order, whatever the price did since.
        if existing["status"] == "FILLED":
            _record(db, alert_id, email, "APPROVED")
        return existing

    price = price_of(ticker)  # before the claim: no slow work between claim and fill/fail
    stop = _num(alert.get("stopPrice"))
    reason = invalid_reason({**alert, "stopPrice": stop, "priceAtAlert": _num(alert.get("priceAtAlert"))},
                            price, settings)
    if reason:
        db.expire_alert(alert_id, reason)
        raise TradeRefused(f"Request expired: {reason}")
    if store is None or not (settings["tradingEnabled"] and account["enabled"] and account["approvalsBuy"]):
        _record(db, alert_id, email, "APPROVED")
        return None

    priced = price is not None and price > 0  # never size against a missing or zero price
    quantity = quantity_for(price, shares, dollars, suggest_quantity(price, stop, settings)) if priced else 0
    today = trading_date()
    order, created = store.claim_buy(account["id"], alert_id, ticker, quantity, today, strategy=alert.get("strategy"),
                                     stop_price=stop, target_price=_num(alert.get("targetPrice")))
    if not created:
        # Another approval owns this order; never fill or fail it from here.
        if order["status"] == "FILLED":
            _record(db, alert_id, email, "APPROVED")
        return order

    try:
        reason = _buy_refusal(alert, today, store, account["id"], settings, price, quantity)
    except Exception:
        store.fail(order["id"], BROKER_ERROR)
        raise
    if reason:
        raise TradeRefused(reason, store.fail(order["id"], reason))

    order = _settle(store, order, lambda broker: broker.buy(ticker, quantity, order["id"]), price)
    _record(db, alert_id, email, "APPROVED")
    return order


def reject(alert, db, email):
    """Record `email`'s rejection, unless their account already bought it (that position is closed, not relabelled)."""
    if alert.get("status") == "INFO":
        raise TradeRefused(INFO_ONLY)
    if _on_sqlite() and get_trades_store().live_buy(account_for(email)["id"], alert["alertId"]):
        raise TradeRefused(ALREADY_BOUGHT)
    _record(db, alert["alertId"], email, "REJECTED")
    return True


def close_position(email, ticker):
    """Sell the caller's whole position, at any hour. Neither the kill switch nor a disabled account blocks Close."""
    price = price_of(ticker)  # before the claim: no slow work between claim and fill/fail
    if price is None:
        raise TradeRefused("Price unavailable.")
    store = get_trades_store()
    # The position is read inside the claim, so a second Close cannot sell it again.
    order = store.claim_sell(account_for(email)["id"], ticker, trading_date())
    if order is None:
        raise LookupError(ticker)
    return _settle(store, order, lambda broker: broker.sell(ticker, order["quantity"], order["id"]), price)


def _enabled_account(email):
    account = account_for(email)
    if not account["enabled"]:
        raise TradeRefused(ACCOUNT_OFF)
    return account


def manual_buy(email, ticker, shares=None, dollars=None):
    """Paper Trading: buy any ticker by hand at the live price, in the caller's mock account. Any hour (fills at the
    latest quote, pre/post-market when closed); no daily cap, no one-position rule and no kill switch: those govern
    the system's approval buys. The account must be turned on."""
    account_id = _enabled_account(email)["id"]
    ticker = ticker.upper()
    price = price_of(ticker)  # before the claim: no slow work between claim and fill/fail
    quantity = quantity_for(price, shares, dollars, 0) if price and price > 0 else 0
    store = get_trades_store()
    reason = _cash_refusal(store, account_id, price, quantity)
    if reason:
        raise TradeRefused(reason)
    order = store.claim_manual_buy(account_id, ticker, quantity, trading_date())
    return _settle(store, order, lambda broker: broker.buy(ticker, quantity, order["id"]), price)


def reset_account(email, starting_cash):
    """Archive the caller's orders and start their mock account over with `starting_cash`."""
    account_id = _enabled_account(email)["id"]
    if not get_trades_store().reset(account_id, starting_cash):
        raise TradeRefused("An order is still being placed. Try again in a moment.")
    return positions_snapshot(email)


def positions_snapshot(email):
    store = get_trades_store()
    positions = []
    account = account_for(email)
    for p in store.open_positions(account["id"]):
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
        "kind": account["kind"],
        "enabled": account["enabled"],
        "approvalsBuy": account["approvalsBuy"],
        "startingCash": account["startingCash"],
        "cash": store.cash(account["id"]),
        "positions": positions,
    }
