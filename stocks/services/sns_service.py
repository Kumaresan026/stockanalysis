import os
import logging
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger('stocks')


class SNSService:
    """
    Clean SNS Service (EB + AWS Academy safe)

    Improvements:
    - No list_topics() (removes API validation issues)
    - Strong validation for ARN
    - Better logging
    - Works with IAM role OR env credentials
    """

    def __init__(self):
        self.region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        self.topic_arn = os.getenv("SNS_TOPIC_ARN")

        self.available = False
        self.client = None



        # Validate ARN
        if not self.topic_arn:
            logger.error("SNS_TOPIC_ARN missing — SNS disabled.")
            return

        if not self.topic_arn.startswith("arn:aws:sns"):
            logger.error(f"Invalid SNS_TOPIC_ARN format: {self.topic_arn}")
            return

        try:
            self.client = boto3.client("sns", region_name=self.region)

            # Minimal validation → no API calls needed
            self.available = True
            logger.info(f"SNS ready (topic_arn={self.topic_arn}, region={self.region})")

        except Exception as e:
            logger.error(f"SNS init failed: {str(e)}")
            self.available = False

    # ───────────────────────────────────────────────
    # Topic
    # ───────────────────────────────────────────────

    def get_topic_arn(self) -> Optional[str]:
        """Return the SNS topic ARN (from env var)."""
        return self.topic_arn

    # ───────────────────────────────────────────────
    # Subscriptions
    # ───────────────────────────────────────────────

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
            code = e.response.get('Error', {}).get('Code', 'Unknown')
            logger.error(f"[SNS] subscribe failed — {code}: {e}")
            return None

    # ───────────────────────────────────────────────
    # Publish
    # ───────────────────────────────────────────────

    def publish(self, subject: str, message: str) -> bool:
        if not self.available:
            logger.error("SNS unavailable — publish skipped.")
            return False

        try:
            logger.info(f"Publishing SNS message to {self.topic_arn}")

            response = self.client.publish(
                TopicArn=self.topic_arn,
                Subject=subject[:100],
                Message=message,
            )

            logger.info(f"SNS publish OK — MessageId={response.get('MessageId')}")
            return True

        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "Unknown")
            msg = e.response.get("Error", {}).get("Message", str(e))

            logger.error(f"[SNS ERROR] code={code} message={msg}")

            if code in ("AuthorizationError", "AccessDeniedException"):
                logger.error("👉 Fix IAM role: add sns:Publish permission")

            elif code in ("NotFound", "InvalidParameter"):
                logger.error("👉 Check SNS_TOPIC_ARN is correct")

            elif code in ("ExpiredToken", "ExpiredTokenException"):
                logger.error("👉 AWS credentials expired — restart lab")

            return False

        except Exception as e:
            logger.error(f"[SNS UNKNOWN ERROR] {str(e)}")
            return False

    # ───────────────────────────────────────────────
    # Alert Publisher
    # ───────────────────────────────────────────────

    def publish_alert(
        self,
        symbol: str,
        condition: str,
        threshold: float,
        current_price: float
    ) -> bool:

        direction = "📈" if "ABOVE" in condition else "📉"

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
            f"Cloud Stock Market Analysis Platform"
        )

        return self.publish(subject, message)