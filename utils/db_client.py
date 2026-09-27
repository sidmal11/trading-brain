import boto3
from botocore.exceptions import ClientError
import os

# Load environment variables
DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Initialize DynamoDB client
if not DYNAMODB_TABLE_NAME:
    print("DYNAMODB_TABLE_NAME environment variable not set. DynamoDB operations will fail.")
    # Create a mock client if env var is missing, to prevent crashes during development/testing
    class MockDynamoDBTable:
        def put_item(self, Item):
            print(f"Mock save_alert called with: {Item}")
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}
        def update_item(self, Key, UpdateExpression, ExpressionAttributeValues):
            print(f"Mock update_alert_status called for Key: {Key}, Update: {UpdateExpression}")
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}
        def get_item(self, Key):
            print(f"Mock get_alert_details called for Key: {Key}")
            # Return a dummy item if needed for testing
            return {"Item": {"alertId": Key.get("alertId"), "status": "pending_human_review"}}
    
    table = MockDynamoDBTable()
else:
    try:
        dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
        table = dynamodb.Table(DYNAMODB_TABLE_NAME)
        # Optional: Check if table exists and is active
        table.load()
        print(f"Connected to DynamoDB table: {DYNAMODB_TABLE_NAME}")
    except ClientError as e:
        print(f"Error connecting to DynamoDB table {DYNAMODB_TABLE_NAME}: {e}")
        # Fallback to mock if connection fails
        class MockDynamoDBTable:
            def put_item(self, Item):
                print(f"Mock save_alert called with: {Item}")
                return {"ResponseMetadata": {"HTTPStatusCode": 200}}
            def update_item(self, Key, UpdateExpression, ExpressionAttributeValues):
                print(f"Mock update_alert_status called for Key: {Key}, Update: {UpdateExpression}")
                return {"ResponseMetadata": {"HTTPStatusCode": 200}}
            def get_item(self, Key):
                print(f"Mock get_alert_details called for Key: {Key}")
                return {"Item": {"alertId": Key.get("alertId"), "status": "pending_human_review"}}
        table = MockDynamoDBTable()

def save_alert(alert_data: dict):
    """
    Saves or updates an alert in DynamoDB.
    alert_data should contain at least: alertId, ticker, timestamp, status, signalDetails, prosAndCons.
    Optional: telegramMessageId, telegramChatId.
    """
    try:
        response = table.put_item(Item=alert_data)
        print(f"Successfully saved alert: {alert_data.get(