"""
SNS Service — boto3 integration for Amazon SNS.

Sends notifications when stock alerts are triggered.
Supports topic creation, email subscriptions, and message publishing.
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
    """

    def __init__(self):
        """Initialize boto3 SNS client with env credentials."""
        self.topic_name = os.getenv('SNS_TOPIC_NAME', 'stock-alerts-topic')
        self.topic_arn = None
        self.available = check_aws_available()
        if not self.available:
            logger.warning("AWS credentials not configured. SNS unavailable.")
            return
        try:
            session = get_boto3_session()
            self.client = session.client('sns')
            logger.info("SNS service initialized successfully.")
        except (NoCredentialsError, Exception) as e:
            self.available = False
            logger.warning(f"SNS init failed: {e}")

    # ── Topic Management ──────────────────────────────────────────────

    def create_topic(self) -> Optional[str]:
        """
        Create an SNS topic programmatically.

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
            logger.info(f"SNS topic created: {self.topic_arn}")
            return self.topic_arn
        except ClientError as e:
            logger.error(f"Error creating SNS topic: {e}")
            return None

    def get_topic_arn(self) -> Optional[str]:
        """Get the ARN of the configured topic."""
        if self.topic_arn:
            return self.topic_arn

        if not self.available:
            return None

        try:
            response = self.client.list_topics()
            for topic in response.get('Topics', []):
                if self.topic_name in topic['TopicArn']:
                    self.topic_arn = topic['TopicArn']
                    return self.topic_arn
            # Topic not found, create it
            return self.create_topic()
        except ClientError as e:
            logger.error(f"Error listing SNS topics: {e}")
            return None

    # ── Subscription Management ───────────────────────────────────────

    def subscribe(self, email: str) -> Optional[str]:
        """
        Subscribe an email address to the alert topic.

        Args:
            email: Email address to subscribe.

        Returns:
            Subscription ARN or None. Note: email subscriptions
            return 'pending confirmation' until the user confirms.
        """
        if not self.available:
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
            logger.info(f"SNS subscription created for {email}: {sub_arn}")
            return sub_arn
        except ClientError as e:
            logger.error(f"Error subscribing {email} to SNS: {e}")
            return None

    def list_subscriptions(self) -> List[Dict[str, str]]:
        """List all subscriptions for the alert topic."""
        if not self.available:
            return []

        topic_arn = self.get_topic_arn()
        if not topic_arn:
            return []

        try:
            response = self.client.list_subscriptions_by_topic(
                TopicArn=topic_arn,
            )
            return [
                {
                    'endpoint': sub['Endpoint'],
                    'protocol': sub['Protocol'],
                    'status': sub['SubscriptionArn'],
                }
                for sub in response.get('Subscriptions', [])
            ]
        except ClientError as e:
            logger.error(f"Error listing SNS subscriptions: {e}")
            return []

    # ── Publishing ────────────────────────────────────────────────────

    def publish(self, subject: str, message: str) -> bool:
        """
        Publish a notification message to the SNS topic.

        Args:
            subject: Email subject line.
            message: Notification message body.

        Returns:
            True on success, False on failure.
        """
        if not self.available:
            logger.warning("SNS unavailable — notification not sent.")
            return False

        topic_arn = self.get_topic_arn()
        if not topic_arn:
            return False

        try:
            self.client.publish(
                TopicArn=topic_arn,
                Subject=subject[:100],  # SNS subject limit
                Message=message,
            )
            logger.info(f"SNS notification published: {subject}")
            return True
        except ClientError as e:
            log_aws_error(e, f"sns.publish topic={self.topic_name}")
            return False

    def publish_alert(self, symbol: str, condition: str,
                      threshold: float, current_price: float) -> bool:
        """
        Publish a stock alert notification.

        Args:
            symbol: Stock symbol.
            condition: Alert condition type.
            threshold: Alert threshold value.
            current_price: Current stock price.

        Returns:
            True on success.
        """
        subject = f"Stock Alert: {symbol} - {condition}"
        message = (
            f"🔔 Stock Alert Triggered\n"
            f"{'=' * 40}\n\n"
            f"Symbol: {symbol}\n"
            f"Condition: {condition}\n"
            f"Threshold: ${threshold:.2f}\n"
            f"Current Price: ${current_price:.2f}\n\n"
            f"This alert was generated by the Cloud Stock Market Analysis Platform.\n"
            f"Log in to your dashboard to manage your alerts."
        )
        return self.publish(subject, message)
