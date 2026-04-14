import os
import logging
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger('stocks')


class SNSService:
    """
    Production-ready SNS Service.

    Fixes:
    - No STS dependency (works with IAM role in EB)
    - Forces region explicitly
    - Requires SNS_TOPIC_ARN (no unreliable list_topics)
    - Clear logging for debugging
    """

    def __init__(self):
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self.topic_arn = os.getenv("SNS_TOPIC_ARN")

        self.available = False
        self.client = None

        if not self.topic_arn:
            logger.error("SNS_TOPIC_ARN not set in environment.")
            return

        try:
            # Use IAM role or env credentials automatically
            session = boto3.Session()
            self.client = session.client("sns", region_name=self.region)

            # Lightweight check → SNS access works
            self.client.list_topics(MaxResults=1)

            self.available = True
            logger.info(f"SNS initialised successfully (region={self.region})")

        except Exception as e:
            logger.error(f"SNS init failed: {str(e)}")
            self.available = False

    # ───────────────────────────────────────────────
    # Publish
    # ───────────────────────────────────────────────

    def publish(self, subject: str, message: str) -> bool:
        if not self.available:
            logger.error("SNS unavailable — cannot publish.")
            return False

        try:
            logger.info(f"Publishing to SNS topic: {self.topic_arn}")

            response = self.client.publish(
                TopicArn=self.topic_arn,
                Subject=subject[:100],
                Message=message,
            )

            logger.info(f"SNS SUCCESS — MessageId={response.get('MessageId')}")
            return True

        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "Unknown")
            msg = e.response.get("Error", {}).get("Message", str(e))

            logger.error(f"[SNS ERROR] code={code} message={msg}")

            if code in ("AuthorizationError", "AccessDeniedException"):
                logger.error("Fix: Ensure IAM role has sns:Publish permission.")
            elif code in ("NotFound", "InvalidParameter"):
                logger.error("Fix: Check SNS_TOPIC_ARN is correct.")
            elif code in ("ExpiredToken", "ExpiredTokenException"):
                logger.error("Fix: Refresh AWS credentials (if using env keys).")

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