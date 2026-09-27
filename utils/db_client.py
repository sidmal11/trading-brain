import boto3
import logging
import time
from botocore.exceptions import ClientError
from config import Config

logger = logging.getLogger(__name__)

class DbClient:
    def __init__(self):
        self.dynamodb = boto3.resource('dynamodb')
        self.table = self.dynamodb.Table(Config.DYNAMODB_TABLE_NAME)

    def save_alert(self, alert_id, ticker, signal_details, pros_cons, chat_id=None, message_id=None):
        """Saves a new alert to DynamoDB."""
        item = {
            'alertId': alert_id,
            'ticker': ticker,
            'timestamp': str(int(time.time())),
            'signalDetails': signal_details,
            'prosAndCons': pros_cons,
            'status': 'pending_human_review'
        }
        if chat_id: item['telegramChatId'] = chat_id
        if message_id: item['telegramMessageId'] = message_id

        try:
            self.table.put_item(Item=item)
            return True
        except ClientError as e:
            logger.error(f"Failed to save alert {alert_id}: {e}")
            return False

    def update_alert_status(self, alert_id, status):
        """Updates the status of an existing alert."""
        try:
            self.table.update_item(
                Key={'alertId': alert_id},
                UpdateExpression="set #s = :status",
                ExpressionAttributeValues={':status': status},
                ExpressionAttributeNames={"#s": "status"}
            )
            return True
        except ClientError as e:
            logger.error(f"Failed to update alert status {alert_id}: {e}")
            return False
