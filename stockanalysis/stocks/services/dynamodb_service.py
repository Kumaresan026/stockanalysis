"""
DynamoDB Service — IAM role authentication via default credential chain.

All boto3 clients are created lazily (on first use) inside properties.
No AWS calls happen during __init__.  Every public method catches all
exceptions and returns a safe default so the app never crashes.
"""

import os
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, List, Optional

from botocore.exceptions import ClientError
from stocks.services.aws_session import get_resource, get_client, log_aws_error

logger = logging.getLogger('stocks')


class DynamoDBService:
    """
    DynamoDB CRUD — uses LabRole instance profile on EB.
    Zero credential injection; boto3 handles auth automatically.
    """

    def __init__(self):
        """Lightweight init — no AWS calls, no credential checks."""
        self.region = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')
        self._dynamodb  = None   # lazy boto3 resource
        self._client    = None   # lazy boto3 client

    # ── Lazy boto3 objects ────────────────────────────────────────────────────

    @property
    def dynamodb(self):
        if self._dynamodb is None:
            self._dynamodb = get_resource('dynamodb')
        return self._dynamodb

    @property
    def client(self):
        if self._client is None:
            self._client = get_client('dynamodb')
        return self._client

    # ── Table Management ──────────────────────────────────────────────────────

    def create_table(self, table_name: str, key_schema: List[Dict],
                     attribute_definitions: List[Dict],
                     billing_mode: str = 'PAY_PER_REQUEST') -> Optional[Any]:
        """Create a DynamoDB table if it doesn't exist."""
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
            log_aws_error(e, f"create_table '{table_name}'")
            return None
        except Exception as e:
            logger.warning(f"[DynamoDB] create_table '{table_name}' failed: {e}")
            return None

    def create_stock_tables(self):
        """Create all required DynamoDB tables for the platform."""
        tables = {
            os.getenv('DYNAMODB_STOCKS_TABLE', 'stock_data'): {
                'key_schema': [
                    {'AttributeName': 'symbol',    'KeyType': 'HASH'},
                    {'AttributeName': 'timestamp', 'KeyType': 'RANGE'},
                ],
                'attributes': [
                    {'AttributeName': 'symbol',    'AttributeType': 'S'},
                    {'AttributeName': 'timestamp', 'AttributeType': 'S'},
                ],
            },
            os.getenv('DYNAMODB_WATCHLIST_TABLE', 'user_watchlists'): {
                'key_schema': [
                    {'AttributeName': 'user_id', 'KeyType': 'HASH'},
                    {'AttributeName': 'symbol',  'KeyType': 'RANGE'},
                ],
                'attributes': [
                    {'AttributeName': 'user_id', 'AttributeType': 'S'},
                    {'AttributeName': 'symbol',  'AttributeType': 'S'},
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
                    {'AttributeName': 'symbol',        'KeyType': 'HASH'},
                    {'AttributeName': 'analysis_type', 'KeyType': 'RANGE'},
                ],
                'attributes': [
                    {'AttributeName': 'symbol',        'AttributeType': 'S'},
                    {'AttributeName': 'analysis_type', 'AttributeType': 'S'},
                ],
            },
        }
        for table_name, cfg in tables.items():
            self.create_table(table_name, cfg['key_schema'], cfg['attributes'])

    # ── CRUD Operations ───────────────────────────────────────────────────────

    def put_item(self, table_name: str, item: Dict[str, Any]) -> bool:
        """Insert or replace an item in a DynamoDB table."""
        try:
            table = self.dynamodb.Table(table_name)
            sanitized = json.loads(json.dumps(item), parse_float=Decimal)
            table.put_item(Item=sanitized)
            logger.info(f"Item stored in '{table_name}': {list(item.keys())}")
            return True
        except ClientError as e:
            log_aws_error(e, f"put_item '{table_name}'")
            return False
        except Exception as e:
            logger.warning(f"[DynamoDB] put_item '{table_name}' failed: {e}")
            return False

    def get_item(self, table_name: str, key: Dict[str, Any]) -> Optional[Dict]:
        """Retrieve a single item by primary key."""
        try:
            table    = self.dynamodb.Table(table_name)
            response = table.get_item(Key=key)
            return response.get('Item')
        except ClientError as e:
            log_aws_error(e, f"get_item '{table_name}'")
            return None
        except Exception as e:
            logger.warning(f"[DynamoDB] get_item '{table_name}' failed: {e}")
            return None

    def query_items(self, table_name: str, key_condition_expression,
                    expression_values: Dict[str, Any],
                    limit: int = 100) -> List[Dict]:
        """Query items from a DynamoDB table."""
        try:
            table    = self.dynamodb.Table(table_name)
            response = table.query(
                KeyConditionExpression=key_condition_expression,
                ExpressionAttributeValues=expression_values,
                Limit=limit,
            )
            return response.get('Items', [])
        except ClientError as e:
            log_aws_error(e, f"query_items '{table_name}'")
            return []
        except Exception as e:
            logger.warning(f"[DynamoDB] query_items '{table_name}' failed: {e}")
            return []

    def update_item(self, table_name: str, key: Dict[str, Any],
                    update_expression: str,
                    expression_values: Dict[str, Any]) -> bool:
        """Update an existing item in DynamoDB."""
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
            log_aws_error(e, f"update_item '{table_name}'")
            return False
        except Exception as e:
            logger.warning(f"[DynamoDB] update_item '{table_name}' failed: {e}")
            return False

    def delete_item(self, table_name: str, key: Dict[str, Any]) -> bool:
        """Delete an item from DynamoDB."""
        try:
            table = self.dynamodb.Table(table_name)
            table.delete_item(Key=key)
            logger.info(f"Item deleted from '{table_name}'.")
            return True
        except ClientError as e:
            log_aws_error(e, f"delete_item '{table_name}'")
            return False
        except Exception as e:
            logger.warning(f"[DynamoDB] delete_item '{table_name}' failed: {e}")
            return False

    def scan_table(self, table_name: str, limit: int = 100) -> List[Dict]:
        """Scan and return all items from a table (use sparingly)."""
        try:
            table    = self.dynamodb.Table(table_name)
            response = table.scan(Limit=limit)
            return response.get('Items', [])
        except ClientError as e:
            log_aws_error(e, f"scan_table '{table_name}'")
            return []
        except Exception as e:
            logger.warning(f"[DynamoDB] scan_table '{table_name}' failed: {e}")
            return []

    # ── Convenience Methods ───────────────────────────────────────────────────

    def store_stock_data(self, symbol: str, price: float, volume: int,
                         change_percent: float = 0.0) -> bool:
        """Store a stock data point in DynamoDB."""
        table_name = os.getenv('DYNAMODB_STOCKS_TABLE', 'stock_data')
        item = {
            'symbol':         symbol.upper(),
            'timestamp':      datetime.utcnow().isoformat(),
            'price':          price,
            'volume':         volume,
            'change_percent': change_percent,
        }
        return self.put_item(table_name, item)

    def store_alert_rule(self, alert_id: str, user_id: str, symbol: str,
                         condition: str, threshold: float,
                         username: str = '') -> bool:
        """
        Store an alert rule in DynamoDB.

        Stores both user_id and username so alerts can be recovered by
        username even after EB restarts where user_id may change.
        """
        table_name = os.getenv('DYNAMODB_ALERTS_TABLE', 'alert_rules')
        item = {
            'alert_id':   alert_id,
            'user_id':    str(user_id),
            'username':   username,
            'symbol':     symbol.upper(),
            'condition':  condition,
            'threshold':  threshold,
            'status':     'active',
            'created_at': datetime.utcnow().isoformat(),
        }
        return self.put_item(table_name, item)

    def store_analytics_result(self, symbol: str, analysis_type: str,
                                result_data: Dict) -> bool:
        """Store analytics results in DynamoDB."""
        table_name = os.getenv('DYNAMODB_ANALYTICS_TABLE', 'analytics_results')
        item = {
            'symbol':        symbol.upper(),
            'analysis_type': analysis_type,
            'result_data':   result_data,
            'computed_at':   datetime.utcnow().isoformat(),
        }
        return self.put_item(table_name, item)

    def get_user_alerts(self, user_id: str, status: str = 'active',
                        username: str = '') -> List[Dict]:
        """
        Fetch alert rules for a specific user from DynamoDB.

        Strategy:
        1. Scan by user_id (fast path)
        2. If nothing found AND username provided, scan by username
           (fallback for EB restarts where user_id changed after SQLite wipe)
        """
        table_name = os.getenv('DYNAMODB_ALERTS_TABLE', 'alert_rules')

        def _scan(filter_expr, attr_names, attr_values):
            try:
                table    = self.dynamodb.Table(table_name)
                response = table.scan(
                    FilterExpression=filter_expr,
                    ExpressionAttributeNames=attr_names,
                    ExpressionAttributeValues=attr_values,
                )
                return response.get('Items', [])
            except Exception as e:
                log_aws_error(e, f"get_user_alerts '{table_name}'")
                return []

        # 1. Try by user_id first
        items = _scan(
            'user_id = :uid AND #st = :status',
            {'#st': 'status'},
            {':uid': str(user_id), ':status': status},
        )
        if items:
            logger.info(f"Fetched {len(items)} {status} alerts for user_id={user_id}.")
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
                    f"(user_id fallback)."
                )
                # Update user_id to current session value
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
