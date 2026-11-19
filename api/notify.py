"""Telegram confirmations for mock trades (P2): every buy, refusal and close, wherever it was made. Admin chats only."""
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


def admins(text, telegram=None):
    """Send `text` to every admin chat. A failure is logged, never raised: the trade is already recorded."""
    from utils.telegram_client import TelegramClient

    telegram = telegram or TelegramClient()
    for chat_id in sorted(telegram.admin_chat_ids):
        try:
            telegram.send_text(chat_id, text)
        except Exception:
            logger.exception("Telegram confirmation to %s failed", chat_id)
