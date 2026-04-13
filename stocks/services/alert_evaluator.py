"""
Alert Evaluator — runs directly on the EB web/worker instance.

This module provides evaluate_and_notify() which is called from stock_detail
and from the poll_sqs worker. It evaluates all active alert rules for a given
stock price against the user's alerts stored in DynamoDB, sends SNS
notifications for triggered alerts, and marks them as triggered.

Why this exists:
  When Lambda functions are not deployed (or invocation fails), the app has no
  way to evaluate alerts. This module provides a reliable in-process fallback
  that works even without Lambda, using the same DynamoDB + SNS services
  already initialised in views.py.
"""

import logging
import os
from datetime import datetime
from typing import List, Dict, Any

logger = logging.getLogger('stocks')


def evaluate_and_notify(symbol: str, price: float, volume: int,
                        change_percent: float,
                        dynamodb_service, sns_service) -> List[Dict]:
    """
    Evaluate all active alerts for a stock and send SNS notifications.

    Called from:
    - stock_detail view (every time a stock page is loaded)
    - poll_sqs worker (for every STOCK_UPDATE SQS message)

    Args:
        symbol: Stock ticker (e.g. 'AMZN')
        price: Current price
        volume: Current volume
        change_percent: Change percent today
        dynamodb_service: Initialised DynamoDBService instance
        sns_service: Initialised SNSService instance

    Returns:
        List of triggered alert dicts (empty if none triggered)
    """
    symbol = symbol.upper()

    if price <= 0:
        logger.warning(f"[AlertEval] Skipping {symbol} — invalid price {price}")
        return []

    if not dynamodb_service.available:
        logger.warning(f"[AlertEval] DynamoDB unavailable — cannot evaluate alerts for {symbol}")
        return []

    # Fetch all active alerts for this symbol from DynamoDB
    table_name = os.getenv('DYNAMODB_ALERTS_TABLE', 'alert_rules')
    try:
        table = dynamodb_service.dynamodb.Table(table_name)
        response = table.scan(
            FilterExpression='symbol = :sym AND #st = :status',
            ExpressionAttributeNames={'#st': 'status'},
            ExpressionAttributeValues={
                ':sym': symbol,
                ':status': 'active',
            },
        )
        alert_rules = response.get('Items', [])
    except Exception as e:
        logger.error(f"[AlertEval] Failed to fetch alert rules for {symbol}: {e}")
        return []

    if not alert_rules:
        logger.debug(f"[AlertEval] No active alerts for {symbol}")
        return []

    logger.info(f"[AlertEval] Evaluating {len(alert_rules)} alert(s) for {symbol} "
                f"@ price={price}, volume={volume}, change={change_percent}%")

    triggered = []

    for rule in alert_rules:
        condition = rule.get('condition', '')
        try:
            threshold = float(rule.get('threshold', 0))
        except (TypeError, ValueError):
            logger.warning(f"[AlertEval] Invalid threshold in rule: {rule}")
            continue

        # ── Evaluate condition ────────────────────────────────────────
        is_triggered = False

        if condition == 'PRICE_ABOVE' and price > threshold:
            is_triggered = True
        elif condition == 'PRICE_BELOW' and price < threshold:
            is_triggered = True
        elif condition == 'VOLUME_ABOVE' and volume > threshold:
            is_triggered = True
        elif condition == 'CHANGE_ABOVE' and change_percent > threshold:
            is_triggered = True
        elif condition == 'CHANGE_BELOW' and change_percent < threshold:
            is_triggered = True

        if not is_triggered:
            logger.debug(f"[AlertEval] {symbol} {condition} {threshold}: NOT triggered "
                         f"(price={price}, volume={volume}, change={change_percent})")
            continue

        alert_id = rule.get('alert_id', '')
        user_id = rule.get('user_id', '')

        logger.info(f"[AlertEval] *** TRIGGERED: {symbol} {condition} {threshold} "
                    f"(current price={price}) user_id={user_id} alert_id={alert_id} ***")

        triggered_alert = {
            'alert_id': alert_id,
            'symbol': symbol,
            'condition': condition,
            'threshold': threshold,
            'current_price': price,
            'user_id': user_id,
            'triggered_at': datetime.utcnow().isoformat(),
        }
        triggered.append(triggered_alert)

        # ── Mark as triggered in DynamoDB ─────────────────────────────
        _mark_triggered(dynamodb_service, table_name, alert_id, symbol, price, condition, threshold)

        # ── Also mark in Django SQLite (so UI updates to "triggered") ──
        _mark_triggered_sqlite(alert_id, user_id)

        # ── Send SNS email notification ───────────────────────────────
        _send_sns_notification(sns_service, symbol, condition, threshold, price, user_id)

    if triggered:
        logger.info(f"[AlertEval] {len(triggered)} alert(s) triggered for {symbol}")
    else:
        logger.info(f"[AlertEval] {symbol}: {len(alert_rules)} rule(s) evaluated, none triggered")

    return triggered


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mark_triggered(dynamodb_service, table_name: str, alert_id: str,
                    symbol: str, price: float, condition: str, threshold: float):
    """Update the alert status to 'triggered' in DynamoDB."""
    if not alert_id:
        return
    try:
        from botocore.exceptions import ClientError
        table = dynamodb_service.dynamodb.Table(table_name)
        table.update_item(
            Key={'alert_id': alert_id},
            UpdateExpression=(
                'SET #st = :status, triggered_at = :ts, '
                'triggered_price = :price, triggered_message = :msg'
            ),
            ExpressionAttributeNames={'#st': 'status'},
            ExpressionAttributeValues={
                ':status': 'triggered',
                ':ts': datetime.utcnow().isoformat(),
                ':price': str(price),
                ':msg': f"{symbol} {condition} {threshold} | price={price}",
            },
        )
        logger.info(f"[AlertEval] DynamoDB: marked alert {alert_id} as triggered")
    except Exception as e:
        logger.error(f"[AlertEval] Failed to mark alert {alert_id} as triggered in DynamoDB: {e}")


def _mark_triggered_sqlite(alert_id: str, user_id: str):
    """Mark the corresponding Django SQLite Alert as triggered so the UI updates."""
    if not alert_id:
        return
    try:
        from django.utils import timezone
        from stocks.models import Alert
        updated = Alert.objects.filter(id=int(alert_id)).update(
            status='triggered',
            triggered_at=timezone.now(),
        )
        if updated:
            logger.info(f"[AlertEval] SQLite: marked alert {alert_id} as triggered")
    except Exception as e:
        # SQLite update is best-effort — DynamoDB is the true source of truth
        logger.debug(f"[AlertEval] SQLite update skipped for alert {alert_id}: {e}")


def _send_sns_notification(sns_service, symbol: str, condition: str,
                           threshold: float, current_price: float, user_id: str):
    """Send an email notification via SNS."""
    if not sns_service or not sns_service.available:
        logger.warning(f"[AlertEval] SNS unavailable — notification not sent for {symbol}")
        return

    subject = f"Stock Alert Triggered: {symbol} {condition} ${threshold:.2f}"
    message = (
        f"Stock Alert Triggered\n"
        f"{'=' * 45}\n\n"
        f"Symbol    : {symbol}\n"
        f"Condition : {condition}\n"
        f"Threshold : ${threshold:.2f}\n"
        f"Price Now : ${current_price:.2f}\n"
        f"Time (UTC): {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"Log in to your dashboard to manage this alert:\n"
        f"  https://<your-eb-url>/alerts/\n\n"
        f"— Cloud Stock Market Analysis Platform"
    )

    ok = sns_service.publish(subject=subject, message=message)
    if ok:
        logger.info(f"[AlertEval] SNS email sent for {symbol} {condition}")
    else:
        logger.error(f"[AlertEval] SNS email FAILED for {symbol} {condition} — "
                     f"check SNS topic exists and email subscription is confirmed")
