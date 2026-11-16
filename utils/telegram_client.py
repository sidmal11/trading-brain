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
from html import escape

import requests

from config import Config

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"

# Telegram rejects a sendMessage body over 4096 characters outright, so the LLM
# analysis is truncated rather than risking the whole alert being dropped.
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


def _truncate(body: str) -> str:
    if len(body) <= MAX_MESSAGE_CHARS:
        return body
    return body[: MAX_MESSAGE_CHARS - len(_TRUNCATION_NOTE)] + _TRUNCATION_NOTE


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

    def format_alert(self, ticker: str, signal_details: str, analysis: str) -> str:
        """Build the message body.

        HTML parse mode, not Markdown: LLM output routinely contains `*`, `_`
        and `` ` `` characters, and an unbalanced one makes Telegram reject the
        whole message. With HTML the only characters that matter are escaped here.
        """
        return _truncate(
            f"<b>📈 {escape(ticker)}</b>\n"
            f"<i>{escape(signal_details)}</i>\n\n"
            f"{escape(analysis)}"
        )

    @property
    def admin_chat_ids(self) -> set:
        """Chats that get Approve/Reject buttons. Everyone else gets read-only alerts."""
        admins = {str(self.chat_id)} if self.chat_id else set()
        admins.update(c.strip() for c in Config.ADMIN_CHAT_IDS.split(",") if c.strip())
        return admins

    def _alert_payload(self, chat_id, ticker, signal_details, analysis, alert_id):
        payload = {
            "chat_id": chat_id,
            "text": self.format_alert(ticker, signal_details, analysis),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        # Buttons only for admins: a decision is the operator's, so a subscriber's
        # tap must never reach the decision path.
        if alert_id and str(chat_id) in self.admin_chat_ids:
            payload["reply_markup"] = build_approval_keyboard(alert_id, ticker)
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

    def broadcast_alert(self, ticker, signal_details, analysis, alert_id, chat_ids):
        """Send one alert to every subscriber.

        Returns `(admin_message_id, blocked)`. `admin_message_id` is the id of the
        first admin copy, the one that carries buttons and is stored on the alert.
        `blocked` lists the chats that returned 403 (the user blocked the bot), so
        the caller can stop sending to them.
        """
        if not self.is_configured:
            logger.warning("Telegram not configured; skipping broadcast for %s", ticker)
            return None, []

        admin_message_id = None
        blocked = []
        for chat_id in chat_ids:
            payload = self._alert_payload(chat_id, ticker, signal_details, analysis, alert_id)
            data = self._post("sendMessage", payload)
            if data and data.get("ok"):
                if str(chat_id) in self.admin_chat_ids and admin_message_id is None:
                    admin_message_id = data.get("result", {}).get("message_id")
            elif data and data.get("status") == 403:
                blocked.append(str(chat_id))
        return admin_message_id, blocked

    def send_text(self, chat_id, text: str):
        """Plain reply to a chat, used for subscribe and unsubscribe confirmations."""
        return self._post("sendMessage", {"chat_id": chat_id, "text": text})

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
