"""
SNS Service — IAM role authentication via default credential chain.

No credential injection. boto3 uses the EC2 instance profile (LabRole) on EB.
All methods catch exceptions and fail gracefully.
"""

import os
import logging
from typing import Optional

from botocore.exceptions import ClientError
from stocks.services.aws_session import get_client, log_aws_error

logger = logging.getLogger('stocks')


class SNSService:
    """
    SNS publish / subscribe — uses LabRole instance profile on EB.
    Zero credential injection; boto3 handles auth automatically.
    """

    def __init__(self):
        """Lightweight init — no AWS calls."""
        self.region    = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')
        self.topic_arn = os.getenv('SNS_TOPIC_ARN') or None
        self._client   = None   # lazy

        if not self.topic_arn:
            logger.warning(
                "SNS_TOPIC_ARN not set — SNS disabled. "
                "Set it in EB Console → Configuration → Software → Environment Properties."
            )
        elif not self.topic_arn.startswith('arn:aws:sns:'):
            logger.error(f"SNS_TOPIC_ARN has invalid format: {self.topic_arn}")
            self.topic_arn = None

    # ── Lazy boto3 client ─────────────────────────────────────────────────────

    @property
    def client(self):
        if self._client is None:
            self._client = get_client('sns')
        return self._client

    # ── Availability check (lightweight) ─────────────────────────────────────

    @property
    def available(self) -> bool:
        """True when a valid topic ARN is configured."""
        return bool(self.topic_arn)

    # ── Topic ARN ─────────────────────────────────────────────────────────────

    def get_topic_arn(self) -> Optional[str]:
        """Return the SNS topic ARN."""
        return self.topic_arn

    # ── Subscription ──────────────────────────────────────────────────────────

    def subscribe(self, email: str) -> Optional[str]:
        """
        Subscribe an email address to the SNS topic (idempotent).
        Returns the subscription ARN, or None on failure.
        """
        if not self.topic_arn or not email:
            return None
        try:
            response = self.client.subscribe(
                TopicArn=self.topic_arn,
                Protocol='email',
                Endpoint=email,
                ReturnSubscriptionArn=True,
            )
            sub_arn = response['SubscriptionArn']
            logger.info(f"SNS subscription for {email}: {sub_arn}")
            return sub_arn
        except ClientError as e:
            log_aws_error(e, f'sns.subscribe email={email}')
            return None
        except Exception as e:
            logger.warning(f"[SNS] subscribe failed for {email}: {e}")
            return None

    # ── Publish ───────────────────────────────────────────────────────────────

    def publish(self, subject: str, message: str) -> bool:
        """
        Publish a notification to the SNS topic.

        Returns True on success, False on any failure (never raises).
        """
        if not self.topic_arn:
            logger.warning("SNS topic ARN not configured — publish skipped.")
            return False

        try:
            response = self.client.publish(
                TopicArn=self.topic_arn,
                Subject=subject[:100],  # SNS subject limit
                Message=message,
            )
            msg_id = response.get('MessageId', '?')
            logger.info(f"SNS publish OK — MessageId={msg_id} subject='{subject[:60]}'")
            return True

        except ClientError as e:
            log_aws_error(e, 'sns.publish')
            return False
        except Exception as e:
            logger.warning(f"[SNS] publish failed: {type(e).__name__}: {e}")
            return False

    # ── Alert Helper ──────────────────────────────────────────────────────────

    def publish_alert(self, symbol: str, condition: str,
                      threshold: float, current_price: float) -> bool:
        """Publish a stock alert-triggered notification."""
        direction = 'UP' if 'ABOVE' in condition else 'DOWN'
        subject = (
            f"Stock Alert {direction}: {symbol} "
            f"{condition.replace('_', ' ').title()} ${threshold:.2f}"
        )
        message = (
            f"Stock Alert Triggered\n"
            f"{'=' * 40}\n\n"
            f"Symbol    : {symbol}\n"
            f"Condition : {condition}\n"
            f"Threshold : ${threshold:.2f}\n"
            f"Current   : ${current_price:.2f}\n\n"
            f"Cloud Stock Market Analysis Platform"
        )
        return self.publish(subject, message)