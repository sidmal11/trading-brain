"""Telegram confirmations for mock trades (P2): every buy, refusal and close, to the chats of the account's email."""
import logging

from api.validity import format_qty

logger = logging.getLogger(__name__)


def _money(x):
    return f"${x:,.2f}"


def bought_text(order, alert) -> str:
    qty, fill = order["quantity"], order["fillPrice"]
    text = f"Bought {format_qty(qty)} {order['ticker']} at {_money(fill)} ({_money(qty * fill)}) (MOCK)."
    alerted = alert.get("priceAtAlert")
    try:
        return text + (f" Alerted at {_money(float(alerted))}." if alerted is not None else "")
    except (TypeError, ValueError):  # a malformed stored price: leave the sentence out
        return text


def refused_text(ticker, reason) -> str:
    return f"{ticker} not bought: {reason}"


def sold_text(order, cost_basis) -> str:
    qty, fill = order["quantity"], order["fillPrice"]
    text = f"Sold {format_qty(qty)} {order['ticker']} at {_money(fill)} (MOCK)."
    if cost_basis is None:
        return text
    pnl = qty * fill - cost_basis
    return text + f" P/L {'+' if pnl >= 0 else '-'}{_money(abs(pnl))}."


def to_email(email, text, telegram=None):
    """Send `text` to the chats linked to `email`, plus the admin chats for the owner. Nothing for an unlinked user.

    A failure is logged, never raised: the trade is already recorded.
    """
    from config import Config
    from utils.telegram_client import TelegramClient

    from .accounts import is_owner

    telegram = telegram or TelegramClient()
    chats = set(telegram.admin_chat_ids) if is_owner(email) else set()  # outside the try: a links failure keeps them
    try:
        if Config.STORAGE_BACKEND == "sqlite":  # the links table lives only in the SQLite file
            from utils.telegram_links import telegram_links

            chats |= set(telegram_links().chats_for_email(email))
    except Exception:
        logger.exception("Telegram confirmation recipients could not be read")
    for chat_id in sorted(chats):
        try:
            telegram.send_text(chat_id, text)
        except Exception:
            logger.exception("Telegram confirmation to %s failed", chat_id)
