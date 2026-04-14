"""
Lambda Function: alert_handler

Evaluates alert rules against current stock data.
If an alert condition is met, publishes an SNS notification.

Deployed to AWS Lambda with LabRole IAM role.
boto3 clients are initialized LAZILY (not at module level)
to avoid credential errors when imported locally by Django.
"""

import json
import logging
import os
import time
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, List

from botocore.exceptions import ClientError

try:
    from stock_event_engine.alerts import AlertEngine, AlertRule
    from stock_event_engine.exceptions import AlertEvaluationError
except ImportError:
    AlertEngine = None
    AlertRule   = None

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_LOG_FORMAT = "[{service}] [{action}] [{status}] {detail}"


def _log(service, action, status, detail=""):
    """Structured CloudWatch log entry."""
    logger.info(_LOG_FORMAT.format(
        service=service, action=action, status=status, detail=detail
    ))


# ── Lazy AWS client cache ─────────────────────────────────────────────────────

_region          = None
_dynamodb_resource = None
_sns_client      = None


def _get_region():
    global _region
    if _region is None:
        _region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    return _region


def _get_dynamodb():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        import boto3
        _dynamodb_resource = boto3.resource("dynamodb", region_name=_get_region())
        _log("DynamoDB", "CLIENT_INIT", "OK", f"region={_get_region()}")
    return _dynamodb_resource


def _get_sns():
    global _sns_client
    if _sns_client is None:
        import boto3
        _sns_client = boto3.client("sns", region_name=_get_region())
        _log("SNS", "CLIENT_INIT", "OK", f"region={_get_region()}")
    return _sns_client


# ── Lambda Entry Point ────────────────────────────────────────────────────────

def lambda_handler(event, context):
    """
    Lambda entry point — evaluates active alert rules.

    Accepts two payload formats:
    1. Direct invocation: { "symbol": "AAPL", "price": 180, ... }
    2. SQS records: { "Records": [ { "body": "{...}" } ] }
    """
    start_time      = time.time()
    triggered_alerts = []
    processed        = 0

    _log("Lambda", "INVOKED", "START", f"keys={list(event.keys())}")
    logger.info("alert_handler Lambda invoked.")

    # Parse payload (support both direct invoke and SQS format)
    payloads = _extract_payloads(event)

    for payload in payloads:
        symbol     = payload.get("symbol", "")
        price      = float(payload.get("price", 0))
        volume     = int(payload.get("volume", 0))
        change_pct = float(payload.get("change_percent", 0))

        _log("Lambda", "EVALUATING_ALERTS", "START",
             f"symbol={symbol} price={price}")

        alert_rules = fetch_alert_rules(symbol)
        _log("DynamoDB", "RULES_FETCHED", "OK",
             f"symbol={symbol} count={len(alert_rules)}")

        if not alert_rules:
            logger.info(f"No active alert rules for {symbol}.")
            processed += 1
            continue

        for rule in alert_rules:
            triggered = evaluate_rule(rule, price, volume, change_pct)
            if triggered:
                result = send_sns_notification(rule, symbol, price)
                if result:
                    triggered_alerts.append({
                        "alert_id":  rule.get("alert_id"),
                        "symbol":    symbol,
                        "condition": rule.get("condition"),
                        "threshold": rule.get("threshold"),
                        "price":     price,
                    })
                    mark_triggered(rule.get("alert_id"))

        processed += 1

    duration_ms = int((time.time() - start_time) * 1000)
    _log("Lambda", "INVOKED", "DONE",
         f"payloads={processed} triggered={len(triggered_alerts)} duration_ms={duration_ms}")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "processed":        processed,
            "triggered_alerts": len(triggered_alerts),
            "duration_ms":      duration_ms,
            "alerts":           triggered_alerts,
        }),
    }


# ── Payload Parsing ───────────────────────────────────────────────────────────

def _extract_payloads(event: Dict) -> List[Dict]:
    """Extract one or more stock data payloads from the event."""
    if "Records" in event:
        payloads = []
        for record in event["Records"]:
            body = record.get("body", "{}")
            payloads.append(json.loads(body) if isinstance(body, str) else body)
        return payloads
    # Direct invocation
    return [event]


# ── DynamoDB ──────────────────────────────────────────────────────────────────

def fetch_alert_rules(symbol: str) -> List[Dict]:
    """
    Scan alert_rules DynamoDB table for active rules matching symbol.
    (Full table scan with filter — acceptable for small alert volumes.)
    """
    table_name = os.getenv("DYNAMODB_ALERTS_TABLE", "alert_rules")
    try:
        import boto3.dynamodb.conditions as cond
        table    = _get_dynamodb().Table(table_name)
        response = table.scan(
            FilterExpression=(
                cond.Attr("symbol").eq(symbol) &
                cond.Attr("status").eq("active")
            )
        )
        rules = response.get("Items", [])
        _log("DynamoDB", "SCAN_ALERT_RULES", "OK",
             f"table={table_name} symbol={symbol} found={len(rules)}")
        return rules
    except ClientError as e:
        _log("DynamoDB", "SCAN_ALERT_RULES", "ERROR", str(e))
        logger.error(f"DynamoDB fetch_alert_rules error: {e}")
        return []


def mark_triggered(alert_id: str):
    """Update alert status to 'triggered' in DynamoDB."""
    if not alert_id:
        return
    table_name = os.getenv("DYNAMODB_ALERTS_TABLE", "alert_rules")
    try:
        table = _get_dynamodb().Table(table_name)
        table.update_item(
            Key={"alert_id": alert_id},
            UpdateExpression="SET #s = :s, triggered_at = :t",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "triggered",
                ":t": datetime.utcnow().isoformat(),
            },
        )
        _log("DynamoDB", "MARK_TRIGGERED", "OK", f"alert_id={alert_id}")
    except ClientError as e:
        _log("DynamoDB", "MARK_TRIGGERED", "ERROR", str(e))
        logger.error(f"DynamoDB mark_triggered error: {e}")


# ── Alert Evaluation ──────────────────────────────────────────────────────────

def evaluate_rule(rule: Dict, price: float, volume: int, change_pct: float) -> bool:
    """
    Evaluate a single alert rule against current market data.

    Supported conditions:
        PRICE_ABOVE, PRICE_BELOW, VOLUME_ABOVE, CHANGE_ABOVE, CHANGE_BELOW
    """
    condition = rule.get("condition", "")
    threshold = float(rule.get("threshold", 0))

    result = {
        "PRICE_ABOVE":  price     >  threshold,
        "PRICE_BELOW":  price     <  threshold,
        "VOLUME_ABOVE": volume    >  threshold,
        "CHANGE_ABOVE": change_pct > threshold,
        "CHANGE_BELOW": change_pct < threshold,
    }.get(condition, False)

    if result:
        _log("Evaluator", "RULE_TRIGGERED", "MATCH",
             f"condition={condition} threshold={threshold} "
             f"price={price} volume={volume} change={change_pct}")
    return result


# ── SNS Notification ──────────────────────────────────────────────────────────

def send_sns_notification(rule: Dict, symbol: str, price: float) -> bool:
    """Build and publish an SNS alert notification."""
    topic_name = os.getenv("SNS_TOPIC_NAME", "stock-alerts-topic")
    region     = _get_region()

    # Resolve topic ARN from name
    try:
        response    = _get_sns().list_topics()
        topic_arn   = None
        for t in response.get("Topics", []):
            if t["TopicArn"].endswith(f":{topic_name}"):
                topic_arn = t["TopicArn"]
                break

        if not topic_arn:
            _log("SNS", "TOPIC_LOOKUP", "ERROR", f"topic_name={topic_name} not found")
            logger.error(f"SNS topic '{topic_name}' not found.")
            return False
    except ClientError as e:
        _log("SNS", "TOPIC_LOOKUP", "ERROR", str(e))
        logger.error(f"SNS list_topics error: {e}")
        return False

    condition = rule.get("condition", "")
    threshold = float(rule.get("threshold", 0))
    username  = rule.get("username", "user")

    condition_map = {
        "PRICE_ABOVE":  f"risen ABOVE ${threshold:.2f}",
        "PRICE_BELOW":  f"fallen BELOW ${threshold:.2f}",
        "VOLUME_ABOVE": f"volume exceeded {int(threshold):,}",
        "CHANGE_ABOVE": f"gained more than {threshold:.2f}%",
        "CHANGE_BELOW": f"dropped more than {abs(threshold):.2f}%",
    }
    condition_text = condition_map.get(condition, f"{condition} {threshold}")

    subject = f"[ALERT TRIGGERED] {symbol} has {condition_text}"
    message = (
        f"Hi {username},\n\n"
        f"Your stock alert has TRIGGERED!\n"
        f"{'=' * 50}\n\n"
        f"  Stock     : {symbol}\n"
        f"  Condition : {condition.replace('_', ' ')}\n"
        f"  Threshold : {threshold}\n"
        f"  Current   : ${price:.2f}\n"
        f"  Triggered : {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
        f"{'=' * 50}\n"
        f"Processed by AWS Lambda (alert_handler) via SQS event-driven pipeline.\n"
        f"-- Cloud Stock Market Analysis Platform"
    )

    try:
        _get_sns().publish(TopicArn=topic_arn, Subject=subject, Message=message)
        _log("SNS", "NOTIFICATION_PUBLISHED", "OK",
             f"topic={topic_name} symbol={symbol} condition={condition}")
        logger.info(f"SNS: alert notification published for {symbol} {condition}")
        return True
    except ClientError as e:
        _log("SNS", "NOTIFICATION_PUBLISHED", "ERROR", str(e))
        logger.error(f"SNS publish error: {e}")
        return False
