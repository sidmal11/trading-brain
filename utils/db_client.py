import boto3
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError
from config import Config

logger = logging.getLogger(__name__)

# The vocabulary the dashboard reads. The dashboard filters the approval queue on
# `status`, so a value outside this set is not rendered at all: the writer and
# the reader have to agree on the same three words.
STATUS_PENDING = 'PENDING'
STATUS_APPROVED = 'APPROVED'
STATUS_REJECTED = 'REJECTED'

STATUS_EXPIRED = 'EXPIRED'

# Stored statuses include EXPIRED (set by the system); DECISIONS is what a reviewer may submit.
VALID_STATUSES = frozenset({STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPIRED})
DECISIONS = frozenset({STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED})


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

    def save_alert(self, alert_id, ticker, signal_details, pros_cons, chat_id=None, message_id=None,
                   price=None, strategy='volume', trading_date=None, signal_type='VOLUME_BREAKTHROUGH',
                   confidence=None, checklist=None, entry=None, stop=None, target=None, messages=None):
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
            'strategy': strategy,
            'signalType': signal_type,
        }
        extra = {'confidence': confidence, 'checklist': checklist, 'entryPrice': entry, 'stopPrice': stop,
                 'targetPrice': target}
        # Round-trip through JSON so every float, also inside the checklist, becomes Decimal(str(x)).
        item.update(json.loads(json.dumps({k: v for k, v in extra.items() if v is not None}), parse_float=Decimal))
        if trading_date: item['tradingDate'] = trading_date
        # DynamoDB rejects Python floats; go through str to keep the printed value.
        if price is not None: item['priceAtAlert'] = Decimal(str(price))
        if chat_id: item['telegramChatId'] = chat_id
        if message_id: item['telegramMessageId'] = message_id
        if messages: item['telegramMessages'] = messages  # ids are ints and the rest strings: no Decimal needed

        try:
            self.table.put_item(Item=item)
            return True
        except ClientError as e:
            logger.error(f"Failed to save alert {alert_id}: {e}")
            return False

    def list_alerts(self, limit=200):
        """Return stored alerts, newest first.

        The table is keyed only by `alertId`, so there is no key condition that
        selects "all alerts" and this has to scan. That is acceptable at the
        volume this table sees (a handful of rows per poll, capped at five), but
        it is the reason `limit` exists: an unbounded scan on a table that has
        been running for months would page forever behind the dashboard.

        Sorting is done here rather than in DynamoDB because a scan returns
        items in no meaningful order. Rows with no timestamp sort last instead
        of raising, since a legacy row without one is still worth showing.
        """
        try:
            response = self.table.scan(Limit=limit)
        except ClientError as e:
            logger.error(f"Failed to list alerts: {e}")
            return []

        items = response.get('Items', [])
        return sorted(items, key=lambda item: item.get('timestamp') or '', reverse=True)

    def alert_exists(self, ticker, strategy, trading_date):
        """True when an alert for this ticker, strategy and trading date is stored.

        Scan with a filter (no index on these fields), paged because Limit and
        filters apply before the match. On error returns False: an alert sent
        twice is better than an alert lost.
        """
        kwargs = {'FilterExpression': Attr('ticker').eq(ticker) & Attr('strategy').eq(strategy)
                  & Attr('tradingDate').eq(trading_date)}
        try:
            while True:
                response = self.table.scan(**kwargs)
                if response.get('Items'):
                    return True
                if 'LastEvaluatedKey' not in response:
                    return False
                kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
        except ClientError as e:
            logger.error(f"Failed to check for existing alert {ticker}: {e}")
            return False

    def get_alert(self, alert_id):
        """Return a single alert, or None if the id is not in the table."""
        try:
            response = self.table.get_item(Key={'alertId': alert_id})
        except ClientError as e:
            logger.error(f"Failed to fetch alert {alert_id}: {e}")
            return None

        return response.get('Item')

    # shortcut: no amount prompts on DynamoDB; trading needs SQLite, so its request buttons never offer "Other amount".
    def set_prompt(self, chat_id, alert_id):
        return None

    def get_prompt(self, chat_id):
        return None

    def clear_prompt(self, chat_id):
        return None

    def expire_alert(self, alert_id, reason):
        """PENDING -> EXPIRED with a reason. Returns whether it changed."""
        try:
            self.table.update_item(
                Key={'alertId': alert_id},
                UpdateExpression='SET #s = :expired, expiredReason = :reason, expiredAt = :at',
                ConditionExpression='#s = :pending',
                ExpressionAttributeValues={':expired': STATUS_EXPIRED, ':reason': reason,
                                           ':at': _now_iso(), ':pending': STATUS_PENDING},
                ExpressionAttributeNames={'#s': 'status'},
            )
            return True
        except ClientError as e:
            if e.response.get('Error', {}).get('Code') != 'ConditionalCheckFailedException':
                logger.error(f"Failed to expire alert {alert_id}: {e}")
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
                ConditionExpression='attribute_not_exists(#s) OR #s <> :expired',  # expired is final
                ExpressionAttributeValues={
                    ':status': status,
                    ':reviewed_at': _now_iso(),
                    ':expired': STATUS_EXPIRED,
                },
                ExpressionAttributeNames={'#s': 'status'}
            )
            return True
        except ClientError as e:
            logger.error(f"Failed to update alert status {alert_id}: {e}")
            return False
