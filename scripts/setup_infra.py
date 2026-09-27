import boto3
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_trading_alerts_table():
    dynamodb = boto3.resource('dynamodb', region_name='us-east-1') # Change as needed
    
    try:
        table = dynamodb.create_table(
            TableName='TradingAlerts',
            KeySchema=[
                {'AttributeName': 'alertId', 'KeyType': 'HASH'}
            ],
            AttributeDefinitions=[
                {'AttributeName': 'alertId', 'AttributeType': 'S'}
            ],
            ProvisionedThroughput={
                'ReadCapacityUnits': 5,
                'WriteCapacityUnits': 5
            }
        )
        logger.info("Table status:", table.table_status)
        return table
    except Exception as e:
        logger.error(f"Error creating table: {e}")

if __name__ == '__main__':
    create_trading_alerts_table()
