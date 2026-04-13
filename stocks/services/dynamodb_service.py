"""
DynamoDB Service — boto3 integration for Amazon DynamoDB.

Provides programmatic table management, CRUD operations for:
- stock data
- user watchlists
- alert rules
- analytics results
"""

import os
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, List, Optional

from botocore.exceptions import ClientError, NoCredentialsError
from stocks.services.aws_session import get_boto3_session, check_aws_available, log_aws_error

logger = logging.getLogger('stocks')


class DynamoDBService:
    """
    Service class for DynamoDB operations via boto3.

    Supports AWS Academy temporary credentials via AWS_SESSION_TOKEN.
    """

    def __init__(self):
        """Initialize boto3 DynamoDB resource with env credentials."""
        self.available = check_aws_available()
        if not self.available:
            logger.warning("AWS credentials not configured. DynamoDB unavailable.")
            return
        try:
            session = get_boto3_session()
            self.dynamodb = session.resource('dynamodb')
            self.client = session.client('dynamodb')
            logger.info("DynamoDB service initialized successfully.")
        except (NoCredentialsError, Exception) as e:
            self.available = False
            logger.warning(f"DynamoDB init failed: {e}")

    # ── Table Management ──────────────────────────────────────────────

    def create_table(self, table_name: str, key_schema: List[Dict],
                     attribute_definitions: List[Dict],
                     billing_mode: str = 'PAY_PER_REQUEST') -> Optional[Any]:
        """
        Create a DynamoDB table programmatically if it doesn't exist.

        Args:
            table_name: Name of the table.
            key_schema: Key schema definition.
            attribute_definitions: Attribute type definitions.
            billing_mode: Billing mode (default PAY_PER_REQUEST).

        Returns:
            Table resource or None on failure.
        """
        if not self.available:
            logger.warning(f"Cannot create table '{table_name}': DynamoDB unavailable.")
            return None

        try:
            table = self.dynamodb.create_table(
                TableName=table_name,
                KeySchema=key_schema,
                AttributeDefinitions=attribute_definitions,
                BillingMode=billing_mode,
            )
            table.wait_until_exists()
            logger.info(f"DynamoDB table '{table_name}' created successfully.")
            return table
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceInUseException':
                logger.info(f"Table '{table_name}' already exists.")
                return self.dynamodb.Table(table_name)
            logger.error(f"Error creating table '{table_name}': {e}")
            return None

    def create_stock_tables(self):
        """Create all required DynamoDB tables for the platform."""
        tables = {
            os.getenv('DYNAMODB_STOCKS_TABLE', 'stock_data'): {
                'key_schema': [
                    {'AttributeName': 'symbol', 'KeyType': 'HASH'},
                    {'AttributeName': 'timestamp', 'KeyType': 'RANGE'},
                ],
                'attributes': [
                    {'AttributeName': 'symbol', 'AttributeType': 'S'},
                    {'AttributeName': 'timestamp', 'AttributeType': 'S'},
                ],
            },
            os.getenv('DYNAMODB_WATCHLIST_TABLE', 'user_watchlists'): {
                'key_schema': [
                    {'AttributeName': 'user_id', 'KeyType': 'HASH'},
                    {'AttributeName': 'symbol', 'KeyType': 'RANGE'},
                ],
                'attributes': [
                    {'AttributeName': 'user_id', 'AttributeType': 'S'},
                    {'AttributeName': 'symbol', 'AttributeType': 'S'},
                ],
            },
            os.getenv('DYNAMODB_ALERTS_TABLE', 'alert_rules'): {
                'key_schema': [
                    {'AttributeName': 'alert_id', 'KeyType': 'HASH'},
                ],
                'attributes': [
                    {'AttributeName': 'alert_id', 'AttributeType': 'S'},
                ],
            },
            os.getenv('DYNAMODB_ANALYTICS_TABLE', 'analytics_results'): {
                'key_schema': [
                    {'AttributeName': 'symbol', 'KeyType': 'HASH'},
                    {'AttributeName': 'analysis_type', 'KeyType': 'RANGE'},
                ],
                'attributes': [
                    {'AttributeName': 'symbol', 'AttributeType': 'S'},
                    {'AttributeName': 'analysis_type', 'AttributeType': 'S'},
                ],
            },
        }

        for table_name, config in tables.items():
            self.create_table(table_name, config['key_schema'], config['attributes'])

    # ── CRUD Operations ───────────────────────────────────────────────

    def put_item(self, table_name: str, item: Dict[str, Any]) -> bool:
        """
        Insert or replace an item in a DynamoDB table.

        Args:
            table_name: Target table.
            item: Item dictionary to store.

        Returns:
            True on success, False on failure.
        """
        if not self.available:
            logger.warning("DynamoDB unavailable — put_item skipped.")
            return False

        try:
            table = self.dynamodb.Table(table_name)
            # Convert floats to Decimal for DynamoDB
            sanitized = json.loads(json.dumps(item), parse_float=Decimal)
            table.put_item(Item=sanitized)
            logger.info(f"Item stored in '{table_name}': {list(item.keys())}")
            return True
        except ClientError as e:
            log_aws_error(e, f"put_item '{table_name}'")
            return False

    def get_item(self, table_name: str, key: Dict[str, Any]) -> Optional[Dict]:
        """
        Retrieve a single item by primary key.

        Args:
            table_name: Target table.
            key: Primary key dictionary.

        Returns:
            Item dictionary or None.
        """
        if not self.available:
            return None

        try:
            table = self.dynamodb.Table(table_name)
            response = table.get_item(Key=key)
            return response.get('Item')
        except ClientError as e:
            log_aws_error(e, f"get_item '{table_name}'")
            return None

    def query_items(self, table_name: str, key_condition_expression,
                    expression_values: Dict[str, Any],
                    limit: int = 100) -> List[Dict]:
        """
        Query items from a DynamoDB table.

        Args:
            table_name: Target table.
            key_condition_expression: Boto3 key condition expression.
            expression_values: Expression attribute values.
            limit: Maximum number of items to return.

        Returns:
            List of matching items.
        """
        if not self.available:
            return []

        try:
            table = self.dynamodb.Table(table_name)
            response = table.query(
                KeyConditionExpression=key_condition_expression,
                ExpressionAttributeValues=expression_values,
                Limit=limit,
            )
            return response.get('Items', [])
        except ClientError as e:
            logger.error(f"Error querying '{table_name}': {e}")
            return []

    def update_item(self, table_name: str, key: Dict[str, Any],
                    update_expression: str,
                    expression_values: Dict[str, Any]) -> bool:
        """
        Update an existing item in DynamoDB.

        Args:
            table_name: Target table.
            key: Primary key of the item.
            update_expression: Update expression string.
            expression_values: Expression attribute values.

        Returns:
            True on success, False on failure.
        """
        if not self.available:
            return False

        try:
            table = self.dynamodb.Table(table_name)
            table.update_item(
                Key=key,
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_values,
            )
            logger.info(f"Item updated in '{table_name}'.")
            return True
        except ClientError as e:
            logger.error(f"Error updating item in '{table_name}': {e}")
            return False

    def delete_item(self, table_name: str, key: Dict[str, Any]) -> bool:
        """Delete an item from DynamoDB."""
        if not self.available:
            return False

        try:
            table = self.dynamodb.Table(table_name)
            table.delete_item(Key=key)
            logger.info(f"Item deleted from '{table_name}'.")
            return True
        except ClientError as e:
            log_aws_error(e, f"delete_item '{table_name}'")
            return False

    def scan_table(self, table_name: str, limit: int = 100) -> List[Dict]:
        """Scan and return all items from a table (use sparingly)."""
        if not self.available:
            return []

        try:
            table = self.dynamodb.Table(table_name)
            response = table.scan(Limit=limit)
            return response.get('Items', [])
        except ClientError as e:
            log_aws_error(e, f"scan_table '{table_name}'")
            return []

    # ── Convenience Methods ───────────────────────────────────────────

    def store_stock_data(self, symbol: str, price: float, volume: int,
                         change_percent: float = 0.0) -> bool:
        """Store a stock data point in DynamoDB."""
        table_name = os.getenv('DYNAMODB_STOCKS_TABLE', 'stock_data')
        item = {
            'symbol': symbol.upper(),
            'timestamp': datetime.utcnow().isoformat(),
            'price': price,
            'volume': volume,
            'change_percent': change_percent,
        }
        return self.put_item(table_name, item)

    def store_alert_rule(self, alert_id: str, user_id: str, symbol: str,
                         condition: str, threshold: float,
                         username: str = '') -> bool:
        """
        Store an alert rule in DynamoDB.

        Stores both user_id and username so alerts can be recovered by
        username even after EB restarts where user_id may change (because
        SQLite is wiped and users re-register with new auto-increment IDs).
        """
        table_name = os.getenv('DYNAMODB_ALERTS_TABLE', 'alert_rules')
        item = {
            'alert_id': alert_id,          # UUID — stable across restarts
            'user_id': str(user_id),
            'username': username,           # stable across restarts
            'symbol': symbol.upper(),
            'condition': condition,
            'threshold': threshold,
            'status': 'active',
            'created_at': datetime.utcnow().isoformat(),
        }
        return self.put_item(table_name, item)

    def store_analytics_result(self, symbol: str, analysis_type: str,
                                result_data: Dict) -> bool:
        """Store analytics results in DynamoDB."""
        table_name = os.getenv('DYNAMODB_ANALYTICS_TABLE', 'analytics_results')
        item = {
            'symbol': symbol.upper(),
            'analysis_type': analysis_type,
            'result_data': result_data,
            'computed_at': datetime.utcnow().isoformat(),
        }
        return self.put_item(table_name, item)

    def get_user_alerts(self, user_id: str, status: str = 'active',
                        username: str = '') -> List[Dict]:
        """
        Fetch alert rules for a specific user from DynamoDB.

        Strategy:
        1. Query by user_id (fast path — works when user_id hasn't changed)
        2. If nothing found AND username is provided, query by username
           (fallback for EB restarts where user_id changed after SQLite wipe)

        Args:
            user_id: String representation of the user's primary key.
            status: Alert status to filter by ('active', 'triggered', 'disabled').
            username: Django username — used as fallback when user_id changes.

        Returns:
            List of alert item dicts, or empty list on failure.
        """
        if not self.available:
            return []

        table_name = os.getenv('DYNAMODB_ALERTS_TABLE', 'alert_rules')

        def _scan(filter_expr, attr_names, attr_values):
            try:
                table = self.dynamodb.Table(table_name)
                response = table.scan(
                    FilterExpression=filter_expr,
                    ExpressionAttributeNames=attr_names,
                    ExpressionAttributeValues=attr_values,
                )
                return response.get('Items', [])
            except ClientError as e:
                log_aws_error(e, f"get_user_alerts '{table_name}'")
                return []

        # 1. Try by user_id first
        items = _scan(
            'user_id = :uid AND #st = :status',
            {'#st': 'status'},
            {':uid': str(user_id), ':status': status},
        )
        if items:
            logger.info(f"Fetched {len(items)} {status} alerts for user_id={user_id} from DynamoDB.")
            return items

        # 2. Fallback: try by username (handles post-EB-restart user_id mismatch)
        if username:
            items = _scan(
                'username = :uname AND #st = :status',
                {'#st': 'status'},
                {':uname': username, ':status': status},
            )
            if items:
                logger.info(
                    f"Fetched {len(items)} {status} alerts by username='{username}' "
                    f"(user_id fallback — SQLite may have been reset after EB redeploy)."
                )
                # Update user_id in DynamoDB to match current session
                for item in items:
                    if item.get('user_id') != str(user_id):
                        try:
                            table = self.dynamodb.Table(table_name)
                            table.update_item(
                                Key={'alert_id': item['alert_id']},
                                UpdateExpression='SET user_id = :uid',
                                ExpressionAttributeValues={':uid': str(user_id)},
                            )
                        except Exception:
                            pass
                return items

        logger.info(f"No {status} alerts found for user_id={user_id} / username='{username}'.")
        return []

