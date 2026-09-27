import requests
import json
import logging
from config import Config

logger = logging.getLogger(__name__)

class TelegramClient:
    def __init__(self):
        self.token = Config.TELEGRAM_BOT_TOKEN
        self.chat_id = Config.TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    def send_alert(self, ticker, signal_details, pros_cons):
        """Sends an alert with Approve/Reject buttons."""
        message = (
            f"🚀 **New Signal: {ticker}**\n\n"
            f"**Signal:** {signal_details}\n\n"
            f"**AI Analysis (Pros/Cons):**\n{pros_cons}\n\n"
            f"Do you want to proceed with this trade?"
        )
        
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve", "callback_data": f"approve_{ticker}"},
                    {"text": "❌ Reject", "callback_data": f"reject_{ticker}"}
                ],
                [
                    {"text": "📊 More Info", "url": f"{Config.STOCK_SCREENER_URL}/stocks/{ticker}"}
                ]
            ]
        }
        
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "reply_markup": json.dumps(keyboard)
        }
        
        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            if data.get("ok"):
                return data["result"]["message_id"]
            else:
                logger.error(f"Telegram API error: {data.get('description')}")
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")
        return None

    def answer_callback(self, callback_query_id, text):
        """Answers a callback query to stop the loading animation on the button."""
        url = f"{self.base_url}/answerCallbackQuery"
        payload = {
            "callback_query_id": callback_query_id,
            "text": text
        }
        try:
            requests.post(url, json=payload)
        except Exception as e:
            logger.error(f"Failed to answer Telegram callback: {e}")
