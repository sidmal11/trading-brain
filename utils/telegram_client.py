"""Telegram delivery for trade alerts, with inline Approve/Reject buttons.

This talks to the Bot API over plain HTTPS with `requests` rather than through
`python-telegram-bot`. That library is async-first from v20, which would force
an event loop into a synchronous Lambda handler and pull a large dependency into
the deployment zip for what is two POST requests.

`lambda_function.py` has always imported `TelegramClient` from here; the class
did not exist, so the poll handler raised ImportError before running a single
line. The callbacks the buttons emit are parsed by `parse_callback`, which the
read API's webhook route hands to `DbClient.update_alert_status`.
"""

import logging
import math
import re
from datetime import datetime, timedelta, timezone
from html import escape

import requests

from api.validity import format_qty
from config import Config

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"

# Telegram rejects a sendMessage body over 4096 characters outright, so the LLM
# analysis is truncated rather than risking the whole alert being dropped.
_BOT_USERNAME: str | None = None  # bot_username() cache, one lookup per process

MAX_MESSAGE_CHARS = 4096
_TRUNCATION_NOTE = "\n\n<i>(analysis truncated)</i>"

APPROVE_PREFIX = "approve"
REJECT_PREFIX = "reject"

# The vocabulary db_client.update_alert_status accepts. Mapping lives here so the
# button payload and the stored status cannot drift apart.
_DECISION_BY_PREFIX = {
    APPROVE_PREFIX: "APPROVED",
    REJECT_PREFIX: "REJECTED",
}


def build_approval_keyboard(alert_id: str, ticker: str) -> dict:
    """The inline keyboard sent with an alert.

    Returns the raw `reply_markup` object the Bot API expects. `alert_id` is
    carried in `callback_data` because the tap arrives on a separate request with
    no memory of the alert it came from.
    """
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"{APPROVE_PREFIX}:{alert_id}:{ticker}"},
                {"text": "❌ Reject", "callback_data": f"{REJECT_PREFIX}:{alert_id}:{ticker}"},
            ]
        ]
    }


def parse_callback(callback_data: str):
    """Turn `approve:<alert_id>:<ticker>` into `(alert_id, ticker, status)`.

    Returns None for anything unrecognised. A malformed payload must not be
    guessed at: writing the wrong status would record a trade decision the
    trader never made.
    """
    if not callback_data:
        return None

    parts = callback_data.split(":", 2)
    if len(parts) != 3:
        return None

    prefix, alert_id, ticker = parts
    status = _DECISION_BY_PREFIX.get(prefix)
    if status is None or not alert_id:
        return None

    return alert_id, ticker, status


MAX_SHARES = 1_000_000
MAX_DOLLARS = 10_000_000
AMOUNT_PROMPT = "Send shares (e.g. 5 or 0.25) or dollars (e.g. $10)"
AMOUNT_UNREADABLE = "Could not read that amount. Send shares (e.g. 5 or 0.25) or dollars (e.g. $10)."


def _amount(text: str, cap: float):
    """A positive, finite number no larger than `cap`, or None. Plain decimals only (no 1e3)."""
    if not re.fullmatch(r"\d+(\.\d+)?", text or ""):
        return None
    value = float(text)
    return value if 0 < value <= cap and math.isfinite(value) else None


def _money(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


def build_amount_keyboard(alert_id: str, ticker: str, shares: float, dollars: float) -> dict:
    """Request buttons when trading is on: buy the suggestion, buy dollarsPerTrade, type an amount, reject."""
    q, d = format_qty(shares), _money(dollars)
    return {"inline_keyboard": [
        [{"text": f"Buy {q} sh", "callback_data": f"bq:{alert_id}:{q}"},
         {"text": f"Buy ${d}", "callback_data": f"bd:{alert_id}:{d}"}],
        [{"text": "Other amount", "callback_data": f"ba:{alert_id}"},
         {"text": "❌ Reject", "callback_data": f"{REJECT_PREFIX}:{alert_id}:{ticker}"}],
    ]}


def build_confirm_keyboard(alert_id: str, shares: float | None, dollars: float | None) -> dict:
    """Confirm or cancel a typed amount; the amount rides in the Confirm callback."""
    value = f"s{format_qty(shares)}" if shares is not None else f"d{_money(dollars)}"
    return {"inline_keyboard": [[{"text": "Confirm", "callback_data": f"bc:{alert_id}:{value}"},
                                 {"text": "Cancel", "callback_data": f"bx:{alert_id}"}]]}


def parse_amount_callback(callback_data: str):
    """`(action, alert_id, shares, dollars)` for an amount button, action "buy", "ask" or "cancel"; else None."""
    parts = (callback_data or "").split(":")
    if len(parts) < 2 or not parts[1]:
        return None
    kind, alert_id = parts[0], parts[1]
    if kind in ("ba", "bx") and len(parts) == 2:
        return ("ask" if kind == "ba" else "cancel"), alert_id, None, None
    if len(parts) != 3:
        return None
    value = parts[2]
    if kind == "bc" and value[:1] in ("s", "d"):
        kind, value = ("bq" if value[0] == "s" else "bd"), value[1:]
    if kind == "bq" and (shares := _amount(value, MAX_SHARES)) is not None:
        return "buy", alert_id, shares, None
    if kind == "bd" and (dollars := _amount(value, MAX_DOLLARS)) is not None:
        return "buy", alert_id, None, dollars
    return None


def parse_amount_text(text: str):
    """A typed amount: `5` or `0.25` is shares, `$10` or `10$` is dollars. Returns (shares, dollars) or None."""
    m = re.fullmatch(r"\s*(\$)?\s*([0-9.]+)\s*(\$)?\s*", text or "")
    if not m or (m.group(1) and m.group(3)):
        return None
    if m.group(1) or m.group(3):
        dollars = _amount(m.group(2), MAX_DOLLARS)
        return None if dollars is None else (None, dollars)
    shares = _amount(m.group(2), MAX_SHARES)
    return None if shares is None else (shares, None)


def _truncate(body: str, tail: str = "") -> str:
    """Fit body + tail in one message; only the body is cut, so the tail always survives."""
    if len(body) + len(tail) <= MAX_MESSAGE_CHARS:
        return body + tail
    return body[: MAX_MESSAGE_CHARS - len(_TRUNCATION_NOTE) - len(tail)] + _TRUNCATION_NOTE + tail


class TelegramClient:
    """Sends alerts and acknowledges button taps.

    Every method degrades to a no-op returning None when the bot is not
    configured. A missing token is a deployment gap, not a reason to abort a
    scan that has already done its analysis work.
    """

    def __init__(self, token=None, chat_id=None, timeout=10):
        self.token = token or Config.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or Config.TELEGRAM_CHAT_ID
        self.timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def bot_username(self) -> str | None:
        """The bot's @username from getMe, cached for the process; a failure is not cached, so it is retried."""
        global _BOT_USERNAME
        if not self.token:
            return None
        if _BOT_USERNAME is None:
            data = self._post("getMe", {})
            if data and data.get("ok"):
                _BOT_USERNAME = data.get("result", {}).get("username") or None
        return _BOT_USERNAME

    def _post(self, method: str, payload: dict):
        url = f"{API_BASE}/bot{self.token}/{method}"
        try:
            response = requests.post(url, json=payload, timeout=self.timeout)
        except requests.RequestException as e:
            # The token sits in the URL, so the raw exception is not logged.
            logger.error("Telegram %s failed: %s", method, type(e).__name__)
            return None

        if response.status_code >= 400:
            # Keep the status: a 403 means the user blocked the bot, which the
            # caller uses to stop sending to them.
            logger.error("Telegram %s returned HTTP %s", method, response.status_code)
            return {"ok": False, "status": response.status_code}
        return response.json()

    def format_alert(self, ticker: str, signal_details: str, analysis: str, tail: str = "") -> str:
        """Build the message body.

        HTML parse mode, not Markdown: LLM output routinely contains `*`, `_`
        and `` ` `` characters, and an unbalanced one makes Telegram reject the
        whole message. With HTML the only characters that matter are escaped here.
        """
        return _truncate(
            f"<b>📈 {escape(ticker)}</b>\n"
            f"<i>{escape(signal_details)}</i>\n\n"
            f"{escape(analysis)}",
            tail,
        )

    @property
    def admin_chat_ids(self) -> set:
        """Chats that get Approve/Reject buttons. Everyone else gets read-only alerts."""
        admins = {str(self.chat_id)} if self.chat_id else set()
        admins.update(c.strip() for c in Config.ADMIN_CHAT_IDS.split(",") if c.strip())
        return admins

    def _alert_payload(self, chat_id, ticker, signal_details, analysis, alert_id, order_line=None, keyboard=None,
                       button_chat_ids=None):
        payload = {
            "chat_id": chat_id,
            "text": self.format_alert(ticker, signal_details, analysis),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        # Buttons only for chats that may decide (admins by default, plus linked users the poll names);
        # a plain subscriber's tap must never reach the decision path.
        if alert_id and str(chat_id) in (self.admin_chat_ids if button_chat_ids is None else button_chat_ids):
            payload["reply_markup"] = keyboard or build_approval_keyboard(alert_id, ticker)
            if order_line:
                payload["text"] = self.format_alert(ticker, signal_details, analysis, f"\n\n{escape(order_line)}")
        return payload

    def send_alert(self, ticker, signal_details, analysis, alert_id=None):
        """Post an alert to the configured chat and return its message id, or None.

        `alert_id` is optional only so the signature stays compatible with
        existing callers; without it the message carries no buttons, because a
        tap could not be tied back to a row.
        """
        if not self.is_configured:
            logger.warning(
                "Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID); "
                "skipping alert for %s",
                ticker,
            )
            return None

        if not alert_id:
            logger.warning(
                "No alert_id for %s; sending without approval buttons because a "
                "tap could not be matched to a stored alert.",
                ticker,
            )

        payload = self._alert_payload(self.chat_id, ticker, signal_details, analysis, alert_id)
        data = self._post("sendMessage", payload)
        if not data or not data.get("ok"):
            return None

        return data.get("result", {}).get("message_id")

    def broadcast_alert(self, ticker, signal_details, analysis, alert_id, chat_ids, order_line=None, keyboard=None,
                        button_chat_ids=None):
        """Send one alert to every subscriber.

        Returns `(admin_message_id, blocked, sent)`. `admin_message_id` is the id of
        the first admin copy, the one that carries buttons and is stored on the alert.
        `blocked` lists the chats that returned 403 (the user blocked the bot), so
        the caller can stop sending to them. `sent` is every delivered copy as
        `{"chatId", "messageId", "sentAt"}`, so an expired request can be cleaned out.
        Only chats in `button_chat_ids` (default: the admin chats) get the buttons and the order line.
        """
        if not self.is_configured:
            logger.warning("Telegram not configured; skipping broadcast for %s", ticker)
            return None, [], []

        admin_message_id = None
        blocked = []
        sent = []
        for chat_id in chat_ids:
            payload = self._alert_payload(chat_id, ticker, signal_details, analysis, alert_id, order_line, keyboard,
                                          button_chat_ids)
            data = self._post("sendMessage", payload)
            if data and data.get("ok"):
                message_id = data.get("result", {}).get("message_id")
                sent.append({"chatId": str(chat_id), "messageId": message_id,
                             "sentAt": datetime.now(timezone.utc).isoformat()})
                if str(chat_id) in self.admin_chat_ids and admin_message_id is None:
                    admin_message_id = message_id
            elif data and data.get("status") == 403:
                blocked.append(str(chat_id))
        return admin_message_id, blocked, sent

    def delete_message(self, chat_id, message_id) -> bool:
        data = self._post("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
        return bool(data and data.get("ok"))

    def expire_message(self, chat_id, message_id, sent_at, reason) -> bool:
        """Take an expired request out of a chat; returns whether Telegram took the delete or edit.

        Telegram deletes only messages under 48 hours old; an older one (or a refused
        delete) is edited to the reason, and an edit without reply_markup drops the buttons.
        """
        try:
            recent = datetime.now(timezone.utc) - datetime.fromisoformat(sent_at.replace("Z", "+00:00")) < timedelta(hours=48)
        except (AttributeError, ValueError):
            recent = False
        if recent and self.delete_message(chat_id, message_id):
            return True
        data = self._post("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": reason})
        return bool(data and data.get("ok"))

    def send_text(self, chat_id, text: str, keyboard=None):
        """Plain reply to a chat (confirmations, prompts); `keyboard` adds inline buttons."""
        payload = {"chat_id": chat_id, "text": text}
        if keyboard:
            payload["reply_markup"] = keyboard
        return self._post("sendMessage", payload)

    def clear_buttons(self, chat_id, message_id):
        """Remove the inline buttons from a message (a cancelled amount confirmation)."""
        if not message_id:
            return None
        return self._post("editMessageReplyMarkup",
                          {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}})

    def answer_callback_query(self, callback_query_id: str, text: str = ""):
        """Clear the spinner on the tapped button.

        Telegram shows a loading state on the client until this is called, so
        skipping it leaves the trader looking at a button that appears stuck.
        """
        if not self.token:
            return None

        return self._post(
            "answerCallbackQuery",
            {"callback_query_id": callback_query_id, "text": text},
        )

    def update_message_decision(self, message_id, ticker: str, status: str, chat_id=None):
        """Strip the buttons and stamp the decision onto the original message.

        Without this the Approve/Reject pair stays tappable forever and the
        message gives no sign of which way it went.
        """
        if not self.is_configured or not message_id:
            return None

        mark = "✅ Approved" if status == "APPROVED" else "❌ Rejected"
        return self._post(
            "editMessageReplyMarkup",
            {
                "chat_id": chat_id or self.chat_id,
                "message_id": message_id,
                "reply_markup": {
                    "inline_keyboard": [[{"text": f"{mark} · {ticker}", "callback_data": "noop"}]]
                },
            },
        )
