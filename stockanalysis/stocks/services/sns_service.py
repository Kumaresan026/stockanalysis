"""
SNS Service - boto3 integration for Amazon SNS.

Uses get_boto3_session() from aws_session.py which correctly handles:
  - Local dev: explicit env var credentials (AWS_ACCESS_KEY_ID set in .env)
  - Elastic Beanstalk: IAM role / instance profile (LabRole)
  - AWS Academy: expired placeholder detection (REPLACE_IN_EB_CONSOLE skipped)

If explicit credentials are set to the placeholder string "REPLACE_IN_EB_CONSOLE",
get_boto3_session() falls back to boto3's credential chain (EC2 instance profile),
so SNS calls work via LabRole without needing valid env var credentials.
"""

import os
import logging
from typing import Optional

from botocore.exceptions import ClientError, NoCredentialsError
from stocks.services.aws_session import get_boto3_session, check_aws_available, log_aws_error

logger = logging.getLogger('stocks')


class SNSService:
    """
    SNS Service: dual-mode credentials (env vars OR IAM instance profile).

    Initialisation uses check_aws_available() to verify credentials once,
    then reuses the same session for all API calls.
    SNS_TOPIC_ARN must be set in environment (skips list_topics() API call).
    """

    def __init__(self):
        self.region    = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')
        self.topic_arn = os.getenv('SNS_TOPIC_ARN') or None

        self.available = False
        self.client    = None

        # Validate ARN before doing any AWS call
        if not self.topic_arn:
            logger.warning(
                "SNS_TOPIC_ARN not set in environment — SNS disabled. "
                "Set it in EB Console -> Configuration -> Software -> Environment Properties."
            )
            return

        if not self.topic_arn.startswith('arn:aws:sns:'):
            logger.error(f"SNS_TOPIC_ARN has invalid format: {self.topic_arn}")
            return

        # check_aws_available() handles:
        #   - 'REPLACE_IN_EB_CONSOLE' placeholders   -> uses IAM role instead
        #   - expired session tokens                  -> returns False
        #   - healthy env/IAM credentials             -> returns True
        self.available = check_aws_available()

        if not self.available:
            logger.warning(
                "AWS credentials unavailable — SNS disabled. "
                "On EB: ensure LabRole is the EC2 instance profile. "
                "Locally: check .env credentials are fresh."
            )
            return

        try:
            # get_boto3_session() skips placeholder env vars and uses IAM role
            session     = get_boto3_session()
            self.client = session.client('sns')
            logger.info(f"SNS ready — topic_arn={self.topic_arn} region={self.region}")
        except (NoCredentialsError, Exception) as e:
            self.available = False
            logger.error(f"SNS client creation failed: {e}")

    # ── Topic ARN ─────────────────────────────────────────────────────────

    def get_topic_arn(self) -> Optional[str]:
        """Return the SNS topic ARN (from SNS_TOPIC_ARN env var)."""
        return self.topic_arn

    # ── Subscription ──────────────────────────────────────────────────────

    def subscribe(self, email: str) -> Optional[str]:
        """
        Subscribe an email address to the SNS topic (idempotent).
        AWS SNS subscribe is safe to call multiple times for the same email.
        """
        if not self.available or not email:
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

    # ── Publish ───────────────────────────────────────────────────────────

    def publish(self, subject: str, message: str) -> bool:
        """
        Publish a notification to the SNS topic.

        Args:
            subject: Email subject (truncated to 100 chars - SNS limit).
            message: Notification body text.

        Returns:
            True on success, False on failure.
        """
        if not self.available:
            logger.warning("SNS unavailable — publish skipped.")
            return False

        try:
            response = self.client.publish(
                TopicArn=self.topic_arn,
                Subject=subject[:100],
                Message=message,
            )
            msg_id = response.get('MessageId', '?')
            logger.info(f"SNS publish OK — MessageId={msg_id} subject='{subject[:60]}'")
            return True

        except ClientError as e:
            code = e.response.get('Error', {}).get('Code', 'Unknown')
            msg  = e.response.get('Error', {}).get('Message', str(e))

            if code in ('ExpiredTokenException', 'ExpiredToken'):
                logger.error(
                    "[SNS] PUBLISH FAILED — session token expired. "
                    "Go to AWS Academy -> AWS Details -> AWS CLI -> copy fresh credentials "
                    "-> update in EB Console -> Configuration -> Software -> Env Properties."
                )
            elif code in ('InvalidClientTokenId', 'AuthFailure', 'InvalidAccessKeyId'):
                logger.error(
                    f"[SNS] PUBLISH FAILED — invalid credentials ({code}). "
                    "On EB: leave AWS_ACCESS_KEY_ID as placeholder so LabRole is used. "
                    "Locally: check .env has fresh AWS Academy credentials."
                )
            elif code in ('AuthorizationError', 'AccessDeniedException'):
                logger.error(
                    f"[SNS] PUBLISH FAILED — access denied ({code}): {msg}. "
                    "Ensure LabRole has sns:Publish permission on the topic ARN."
                )
            elif code == 'InvalidParameter':
                logger.error(f"[SNS] PUBLISH FAILED — invalid parameter: {msg}")
            else:
                logger.error(f"[SNS] PUBLISH FAILED — {code}: {msg}")
            return False

    # ── Alert Helper ──────────────────────────────────────────────────────

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