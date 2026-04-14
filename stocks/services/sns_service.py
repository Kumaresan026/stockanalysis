"""
SNS Service — boto3 integration for Amazon SNS.

Sends notifications when stock alerts are triggered.
Supports topic creation, email subscriptions, and message publishing.

Production hardening:
- If SNS_TOPIC_ARN env var is set, the ARN lookup (list_topics API call)
  is skipped entirely — faster and more resilient.
- publish() catches specific boto3 error codes and logs actionable messages.
- subscribe() is idempotent (safe to call multiple times for the same email).
"""

import os
import json
import logging
from typing import Optional, Dict, Any, List

from botocore.exceptions import ClientError, NoCredentialsError
from stocks.services.aws_session import get_boto3_session, check_aws_available, log_aws_error

logger = logging.getLogger('stocks')


class SNSService:
    """
    Service class for Amazon SNS operations via boto3.
    Supports AWS Academy temporary credentials via AWS_SESSION_TOKEN.

    Performance: set SNS_TOPIC_ARN in environment to bypass list_topics()
    and reduce initialisation to a single client creation.
    """

    def __init__(self):
        """Initialize boto3 SNS client with env credentials."""
        self.topic_name = os.getenv('SNS_TOPIC_NAME', 'stock-alerts-topic')
        self.topic_arn  = os.getenv('SNS_TOPIC_ARN') or None  # fast-path: skip list_topics
        self.available  = check_aws_available()

        if not self.available:
            logger.warning("AWS credentials not configured. SNS unavailable.")
            return
        try:
            session = get_boto3_session()
            self.client = session.client('sns')
            if self.topic_arn:
                logger.info(f"SNS service initialised (ARN from env): {self.topic_arn}")
            else:
                logger.info("SNS service initialised successfully.")
        except (NoCredentialsError, Exception) as e:
            self.available = False
            logger.warning(f"SNS init failed: {e}")

    # ── Topic Management ──────────────────────────────────────────────

    def create_topic(self) -> Optional[str]:
        """
        Create an SNS topic (idempotent — safe to call if topic already exists).

        Returns:
            Topic ARN or None on failure.
        """
        if not self.available:
            return None

        try:
            response = self.client.create_topic(
                Name=self.topic_name,
                Attributes={
                    'DisplayName': 'Stock Platform Alerts',
                },
            )
            self.topic_arn = response['TopicArn']
            logger.info(f"SNS topic created/confirmed: {self.topic_arn}")
            return self.topic_arn
        except ClientError as e:
            log_aws_error(e, "sns.create_topic")
            return None

    def get_topic_arn(self) -> Optional[str]:
        """
        Return the SNS topic ARN.

        Priority:
        1. Already resolved (self.topic_arn set from env or previous call)
        2. list_topics() search
        3. create_topic() as last resort
        """
        if self.topic_arn:
            return self.topic_arn

        if not self.available:
            return None

        try:
            paginator = self.client.get_paginator('list_topics')
            for page in paginator.paginate():
                for topic in page.get('Topics', []):
                    if self.topic_name in topic['TopicArn']:
                        self.topic_arn = topic['TopicArn']
                        logger.info(f"SNS topic found: {self.topic_arn}")
                        return self.topic_arn
            # Not found — create it
            logger.info(f"SNS topic '{self.topic_name}' not found — creating it.")
            return self.create_topic()
        except ClientError as e:
            log_aws_error(e, "sns.list_topics")
            return None

    # ── Subscription Management ───────────────────────────────────────

    def subscribe(self, email: str) -> Optional[str]:
        """
        Subscribe an email address to the alert topic (idempotent).

        AWS SNS subscribe is safe to call multiple times for the same
        email — it returns the existing subscription ARN if already confirmed.

        Args:
            email: Email address to subscribe.

        Returns:
            Subscription ARN or 'pending confirmation', or None on failure.
        """
        if not self.available or not email:
            return None

        topic_arn = self.get_topic_arn()
        if not topic_arn:
            return None

        try:
            response = self.client.subscribe(
                TopicArn=topic_arn,
                Protocol='email',
                Endpoint=email,
                ReturnSubscriptionArn=True,
            )
            sub_arn = response['SubscriptionArn']
            logger.info(f"SNS subscription for {email}: {sub_arn}")
            return sub_arn
        except ClientError as e:
            log_aws_error(e, f"sns.subscribe email={email}")
            return None

    def list_subscriptions(self) -> List[Dict[str, str]]:
        """List all subscriptions for the alert topic."""
        if not self.available:
            return []

        topic_arn = self.get_topic_arn()
        if not topic_arn:
            return []

        try:
            response = self.client.list_subscriptions_by_topic(TopicArn=topic_arn)
            return [
                {
                    'endpoint': sub['Endpoint'],
                    'protocol': sub['Protocol'],
                    'status':   sub['SubscriptionArn'],
                }
                for sub in response.get('Subscriptions', [])
            ]
        except ClientError as e:
            log_aws_error(e, "sns.list_subscriptions_by_topic")
            return []

    # ── Publishing ────────────────────────────────────────────────────

    def publish(self, subject: str, message: str) -> bool:
        """
        Publish a notification message to the SNS topic.

        Args:
            subject: Email subject line (truncated to 100 chars — SNS limit).
            message: Notification message body.

        Returns:
            True on success, False on failure.
        """
        if not self.available:
            logger.warning("SNS unavailable — notification not sent.")
            return False

        topic_arn = self.get_topic_arn()
        if not topic_arn:
            logger.error(
                "SNS topic ARN not found. "
                "Set SNS_TOPIC_ARN in environment or run: python manage.py init_aws_resources"
            )
            return False

        try:
            response = self.client.publish(
                TopicArn=topic_arn,
                Subject=subject[:100],   # SNS subject limit
                Message=message,
            )
            msg_id = response.get('MessageId', '?')
            logger.info(f"SNS published OK — MessageId={msg_id} subject='{subject[:60]}'")
            return True
        except ClientError as e:
            code = e.response.get('Error', {}).get('Code', 'Unknown')
            msg  = e.response.get('Error', {}).get('Message', str(e))

            if code in ('ExpiredTokenException', 'ExpiredToken'):
                logger.error(
                    "[SNS] PUBLISH FAILED — AWS_SESSION_TOKEN EXPIRED. "
                    "Refresh: AWS Academy → AWS Details → AWS CLI → copy credentials → "
                    "update in EB Console → Configuration → Software → Environment Properties."
                )
            elif code in ('AuthorizationError', 'AccessDeniedException'):
                logger.error(
                    f"[SNS] PUBLISH FAILED — ACCESS DENIED (code={code}). "
                    f"Message: {msg}. "
                    "Ensure LabRole has sns:Publish permission on the topic ARN."
                )
            elif code == 'InvalidParameter':
                logger.error(f"[SNS] PUBLISH FAILED — Invalid parameter: {msg}")
            elif code == 'KMSDisabledException':
                logger.error("[SNS] PUBLISH FAILED — KMS key is disabled.")
            else:
                logger.error(f"[SNS] PUBLISH FAILED — code={code}: {msg}")
            return False

    def publish_alert(self, symbol: str, condition: str,
                      threshold: float, current_price: float) -> bool:
        """
        Publish a stock alert triggered notification.

        Args:
            symbol:        Stock symbol.
            condition:     Alert condition type (e.g. 'PRICE_ABOVE').
            threshold:     Alert threshold value.
            current_price: Current stock price.

        Returns:
            True on success.
        """
        direction = "📈" if 'ABOVE' in condition else "📉"
        subject = (
            f"{direction} Stock Alert: {symbol} — "
            f"{condition.replace('_', ' ').title()} ${threshold:.2f}"
        )
        message = (
            f"🔔 Stock Alert Triggered\n"
            f"{'=' * 40}\n\n"
            f"Symbol    : {symbol}\n"
            f"Condition : {condition}\n"
            f"Threshold : ${threshold:.2f}\n"
            f"Current   : ${current_price:.2f}\n\n"
            f"This alert was generated by the Cloud Stock Market Analysis Platform.\n"
            f"Log in to your dashboard to manage your alerts."
        )
        return self.publish(subject, message)
