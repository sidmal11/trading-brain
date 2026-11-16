"""Check the Telegram bot end to end: token, chat, and a real alert with buttons.

    python scripts/check_telegram.py

Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in trading_brain/.env or the
environment. Tapping the buttons does nothing until the webhook has a public
URL; this only proves delivery.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import requests  # noqa: E402

from utils.telegram_client import API_BASE, TelegramClient  # noqa: E402

client = TelegramClient()
if not client.is_configured:
    sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in trading_brain/.env first.")

me = requests.get(f"{API_BASE}/bot{client.token}/getMe", timeout=10).json()
if not me.get("ok"):
    sys.exit(f"Token rejected: {me.get('description')}")
print(f"Token OK: bot @{me['result']['username']}")

message_id = client.send_alert(
    "TEST",
    "Connection test from scripts/check_telegram.py",
    "Pros\n- Bot token works\n- Chat id works\nCons\n- Buttons need the webhook deployed",
    alert_id="test-alert",
)
if message_id is None:
    sys.exit(
        "Send failed. Usual causes: wrong TELEGRAM_CHAT_ID, or you have not "
        "pressed Start on the bot (or added it to the group) yet."
    )
print(f"Message delivered, id {message_id}. Check Telegram.")
