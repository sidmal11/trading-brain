import boto3
import logging
from datetime import datetime, timezone
from botocore.exceptions import ClientError
from config import Config

logger = logging.getLogger(__name__)

# The vocabulary the dashboard reads. The dashboard filters the approval queue on
# `status`, so a value outside this set is not rendered at all: the writer and
# the reader have to agree on the same three words.
STATUS_PENDING = 'PENDING'
STATUS_APPROVED = 'APPROVED'
STATUS_REJECTED = 'REJECTED'

VALID_STATUSES = frozenset({STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED})


def _now_iso():
    """UTC now, in the format the dashboard parses.

    `isoformat()` on an aware UTC datetime gives `2026-09-28T10:30:00.123456+00:00`
    with microseconds and a numeric offset. That is deliberately not normalised to
    the `...Z` spelling JavaScript's `toISOString` produces: the reader handles
    both, and truncating to milliseconds would only throw information away.
    """
    return datetime.now(timezone.utc).isoformat()


class DbClient:
    def __init__(self):
        self.dynamodb = boto3.resource('dynamodb')
        self.table = self.dynamodb.Table(Config.DYNAMODB_TABLE_NAME)

    def save_alert(self, alert_id, ticker, signal_details, pros_cons, chat_id=None, message_id=None):
        """Saves a new alert to DynamoDB.

        Field names here are the contract `types/market.ts` declares. `aiProsCons`
        and ISO-8601 `timestamp` used to be `prosAndCons` and unix seconds, which
        the dashboard could not read: it read `aiProsCons` as undefined and the
        approval queue rendered empty.
        """
        item = {
            'alertId': alert_id,
            'ticker': ticker,
            'timestamp': _now_iso(),
            'signalDetails': signal_details,
            'aiProsCons': pros_cons,
            'status': STATUS_PENDING,
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
        """Records a review decision, stamping when it was made.

        `reviewedAt` is written here rather than by the caller. The dashboard
        shows it next to the decision, and a status change with no timestamp
        leaves a row that claims a review happened without saying when.
        """
        if status not in VALID_STATUSES:
            raise ValueError(
                f"Unrecognised status {status!r}. Expected one of {sorted(VALID_STATUSES)}."
            )

        try:
            self.table.update_item(
                Key={'alertId': alert_id},
                UpdateExpression='SET #s = :status, reviewedAt = :reviewed_at',
                ExpressionAttributeValues={
                    ':status': status,
                    ':reviewed_at': _now_iso(),
                },
                ExpressionAttributeNames={'#s': 'status'}
            )
            return True
        except ClientError as e:
            logger.error(f"Failed to update alert status {alert_id}: {e}")
            return False
