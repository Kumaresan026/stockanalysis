"""
Lambda Function: alert_handler

Evaluates alert rules against current stock data.
If an alert condition is met, triggers an SNS notification.

Designed to be deployed as an AWS Lambda function,
triggered by SQS or invoked directly via boto3.
"""

import json
import logging
import os
import time
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, List

import boto3
from botocore.exceptions import ClientError

# Import stock_event_engine for alert evaluation
try:
    from stock_event_engine.alerts import AlertEngine, AlertRule
    from stock_event_engine.exceptions import AlertEvaluationError
except ImportError:
    AlertEngine = None
    AlertRule = None

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb', region_name=os.getenv('AWS_DEFAULT_REGION', 'us-east-1'))
sns_client = boto3.client('sns', region_name=os.getenv('AWS_DEFAULT_REGION', 'us-east-1'))
cloudwatch_logs = boto3.client('logs', region_name=os.getenv('AWS_DEFAULT_REGION', 'us-east-1'))


def lambda_handler(event, context):
    """
    Lambda entry point — evaluates stock alerts.

    Can be triggered by:
    1. SQS event (primary) — when stock data updates
    2. Direct invocation via boto3.invoke() — for testing

    Args:
        event: SQS event with Records, or direct invocation payload.
        context: Lambda context.

    Returns:
        Processing result with triggered alerts.
    """
    start_time = time.time()
    triggered_alerts = []
    processed = 0

    logger.info("alert_handler Lambda invoked.")

    # Handle SQS event format
    records = event.get('Records', [])
    if records:
        for record in records:
            try:
                body = json.loads(record.get('body', '{}'))
                result = evaluate_alerts_for_stock(body)
                triggered_alerts.extend(result)
                processed += 1
            except Exception as e:
                logger.error(f"Error processing alert record: {e}")
    else:
        # Direct invocation
        try:
            result = evaluate_alerts_for_stock(event)
            triggered_alerts.extend(result)
            processed += 1
        except Exception as e:
            logger.error(f"Error in direct invocation: {e}")

    # Send SNS notifications for triggered alerts
    for alert in triggered_alerts:
        send_alert_notification(alert)

    duration_ms = int((time.time() - start_time) * 1000)

    logger.info(
        f"alert_handler completed: {processed} events processed, "
        f"{len(triggered_alerts)} alerts triggered, "
        f"duration: {duration_ms}ms"
    )

    return {
        'statusCode': 200,
        'body': json.dumps({
            'processed': processed,
            'triggered_count': len(triggered_alerts),
            'triggered_alerts': triggered_alerts,
            'duration_ms': duration_ms,
        }, default=str)
    }


def evaluate_alerts_for_stock(event_data: Dict[str, Any]) -> List[Dict]:
    """
    Evaluate all alert rules for a given stock.

    1. Get the stock symbol and current price from the event
    2. Fetch all active alert rules for this symbol from DynamoDB
    3. Evaluate each rule using stock_event_engine.AlertEngine
    4. Return list of triggered alerts

    Args:
        event_data: Event payload with symbol and price.

    Returns:
        List of triggered alert dictionaries.
    """
    symbol = event_data.get('symbol', '').upper()
    price = float(event_data.get('price', 0))
    volume = int(event_data.get('volume', 0))
    change_pct = float(event_data.get('change_percent', 0))

    if not symbol or price <= 0:
        logger.warning(f"Invalid event data: {event_data}")
        return []

    # Fetch active alerts for this symbol from DynamoDB
    alert_rules = fetch_alert_rules(symbol)
    if not alert_rules:
        logger.info(f"No active alerts for {symbol}")
        return []

    triggered = []

    # Build stock data dict for AlertEngine
    stock_data = {
        symbol: {
            'price': price,
            'volume': volume,
            'change_percent': change_pct,
        }
    }

    if AlertEngine and AlertRule:
        # Use stock_event_engine for evaluation
        engine = AlertEngine()
        for rule_data in alert_rules:
            try:
                rule = AlertRule(
                    symbol=rule_data['symbol'],
                    condition=rule_data['condition'],
                    threshold=float(rule_data['threshold']),
                    user_id=rule_data.get('user_id', ''),
                )
                engine.add_rule(rule)
            except Exception as e:
                logger.error(f"Invalid alert rule: {e}")

        triggered_results = engine.evaluate(stock_data)
        for result in triggered_results:
            triggered.append({
                'symbol': symbol,
                'condition': result['rule']['condition'],
                'threshold': result['rule']['threshold'],
                'current_price': price,
                'message': result['message'],
                'user_id': result['rule']['user_id'],
                'triggered_at': datetime.utcnow().isoformat(),
            })

            # Update alert status in DynamoDB
            mark_alert_triggered(
                result['rule'].get('alert_id', ''),
                symbol,
                result['message']
            )
    else:
        # Fallback: simple evaluation without library
        for rule_data in alert_rules:
            condition = rule_data.get('condition', '')
            threshold = float(rule_data.get('threshold', 0))

            is_triggered = False
            if condition == 'PRICE_ABOVE' and price > threshold:
                is_triggered = True
            elif condition == 'PRICE_BELOW' and price < threshold:
                is_triggered = True

            if is_triggered:
                triggered.append({
                    'symbol': symbol,
                    'condition': condition,
                    'threshold': threshold,
                    'current_price': price,
                    'message': f"{symbol} {condition} threshold {threshold} (current: {price})",
                    'user_id': rule_data.get('user_id', ''),
                    'triggered_at': datetime.utcnow().isoformat(),
                })

    logger.info(f"Evaluated {len(alert_rules)} alerts for {symbol}: "
                f"{len(triggered)} triggered")

    return triggered


def fetch_alert_rules(symbol: str) -> List[Dict]:
    """Fetch active alert rules for a symbol from DynamoDB."""
    try:
        table_name = os.getenv('DYNAMODB_ALERTS_TABLE', 'alert_rules')
        table = dynamodb.Table(table_name)

        # Scan for alerts matching this symbol
        # (In production, use a GSI for efficient querying)
        response = table.scan(
            FilterExpression='symbol = :sym AND #st = :status',
            ExpressionAttributeNames={'#st': 'status'},
            ExpressionAttributeValues={
                ':sym': symbol,
                ':status': 'active',
            },
        )
        return response.get('Items', [])
    except ClientError as e:
        logger.error(f"Error fetching alert rules: {e}")
        return []


def mark_alert_triggered(alert_id: str, symbol: str, message: str):
    """Update alert status to 'triggered' in DynamoDB."""
    if not alert_id:
        return

    try:
        table_name = os.getenv('DYNAMODB_ALERTS_TABLE', 'alert_rules')
        table = dynamodb.Table(table_name)
        table.update_item(
            Key={'alert_id': alert_id},
            UpdateExpression='SET #st = :status, triggered_at = :ts, message = :msg',
            ExpressionAttributeNames={'#st': 'status'},
            ExpressionAttributeValues={
                ':status': 'triggered',
                ':ts': datetime.utcnow().isoformat(),
                ':msg': message,
            },
        )
    except ClientError as e:
        logger.error(f"Error updating alert status: {e}")


def send_alert_notification(alert: Dict[str, Any]):
    """Send an SNS notification for a triggered alert."""
    try:
        topic_name = os.getenv('SNS_TOPIC_NAME', 'stock-alerts-topic')

        # Get topic ARN
        response = sns_client.list_topics()
        topic_arn = None
        for topic in response.get('Topics', []):
            if topic_name in topic['TopicArn']:
                topic_arn = topic['TopicArn']
                break

        if not topic_arn:
            logger.warning("SNS topic not found — notification not sent.")
            return

        subject = f"🔔 Stock Alert: {alert['symbol']} - {alert['condition']}"
        message = (
            f"Stock Alert Triggered\n"
            f"{'=' * 40}\n\n"
            f"Symbol: {alert['symbol']}\n"
            f"Condition: {alert['condition']}\n"
            f"Threshold: ${alert['threshold']:.2f}\n"
            f"Current Price: ${alert['current_price']:.2f}\n"
            f"Time: {alert['triggered_at']}\n\n"
            f"Message: {alert.get('message', '')}\n\n"
            f"— Cloud Stock Market Analysis Platform"
        )

        sns_client.publish(
            TopicArn=topic_arn,
            Subject=subject[:100],
            Message=message,
        )
        logger.info(f"Alert notification sent for {alert['symbol']}")

        # Log to CloudWatch
        log_alert_trigger(alert)

    except ClientError as e:
        logger.error(f"Error sending SNS notification: {e}")


def log_alert_trigger(alert: Dict[str, Any]):
    """Log alert trigger event to CloudWatch."""
    try:
        message = (
            f"[ALERT_TRIGGER] Symbol: {alert['symbol']} | "
            f"Condition: {alert['condition']} | "
            f"Threshold: {alert['threshold']} | "
            f"Price: {alert['current_price']}"
        )
        logger.info(message)
    except Exception as e:
        logger.error(f"Error logging alert: {e}")
