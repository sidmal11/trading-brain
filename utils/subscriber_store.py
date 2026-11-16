"""Telegram subscribers: the chats that receive trade alerts.

Keyed by chat id, which is the id Telegram gives a private chat (the user's own
id). A subscriber is active until they send /stop or block the bot.
"""

import logging
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

from config import Config

logger = logging.getLogger(__name__)


class SubscriberStore:
    def __init__(self, table=None):
        if table is None:
            table = boto3.resource("dynamodb").Table(Config.TELEGRAM_SUBSCRIBERS_TABLE)
        self.table = table

    def subscribe(self, chat_id, username=None, first_name=None):
        """Add or reactivate a subscriber. Returns False on a storage error."""
        item = {
            "chatId": str(chat_id),
            "active": True,
            "subscribedAt": datetime.now(timezone.utc).isoformat(),
        }
        if username:
            item["username"] = username
        if first_name:
            item["firstName"] = first_name
        try:
            self.table.put_item(Item=item)
            return True
        except ClientError as e:
            logger.error("Failed to subscribe %s: %s", chat_id, e)
            return False

    def deactivate(self, chat_id):
        """Stop alerts to this chat. Used on /stop and when Telegram reports the bot blocked."""
        try:
            self.table.update_item(
                Key={"chatId": str(chat_id)},
                UpdateExpression="SET active = :off",
                ExpressionAttributeValues={":off": False},
            )
            return True
        except ClientError as e:
            logger.error("Failed to deactivate %s: %s", chat_id, e)
            return False

    def active_chat_ids(self):
        """Every active subscriber's chat id. A scan: fine at the size this serves."""
        chat_ids = []
        kwargs = {"FilterExpression": "active = :on", "ExpressionAttributeValues": {":on": True}}
        try:
            while True:
                response = self.table.scan(**kwargs)
                chat_ids.extend(item["chatId"] for item in response.get("Items", []))
                if "LastEvaluatedKey" not in response:
                    return chat_ids
                kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        except ClientError as e:
            logger.error("Failed to list subscribers: %s", e)
            return chat_ids
