"""
SQS Service — boto3 integration for Amazon SQS.

Provides message queue operations for event-driven stock processing.
Messages are sent when stock data updates, alerts are created,
or analytics need processing.
"""

import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

from botocore.exceptions import ClientError, NoCredentialsError
from stocks.services.aws_session import get_boto3_session, check_aws_available

logger = logging.getLogger('stocks')


class SQSService:
    """
    Service class for Amazon SQS operations via boto3.
    Supports AWS Academy temporary credentials via AWS_SESSION_TOKEN.
    """

    def __init__(self):
        """Initialize boto3 SQS client with env credentials."""
        self.queue_name = os.getenv('SQS_QUEUE_NAME', 'stock-events-queue')
        self.queue_url = None
        self.available = check_aws_available()
        if not self.available:
            logger.warning("AWS credentials not configured. SQS unavailable.")
            return
        try:
            session = get_boto3_session()
            self.client = session.client('sqs')
            logger.info("SQS service initialized successfully.")
        except (NoCredentialsError, Exception) as e:
            self.available = False
            logger.warning(f"SQS init failed: {e}")

    def create_queue(self) -> Optional[str]:
        """
        Create the SQS queue if it doesn't exist.

        Returns:
            Queue URL or None.
        """
        if not self.available:
            return None

        try:
            response = self.client.create_queue(
                QueueName=self.queue_name,
                Attributes={
                    'DelaySeconds': '0',
                    'MessageRetentionPeriod': '345600',  # 4 days
                    'VisibilityTimeout': '60',
                    'ReceiveMessageWaitTimeSeconds': '10',  # Long polling
                },
            )
            self.queue_url = response['QueueUrl']
            logger.info(f"SQS queue created/retrieved: {self.queue_url}")
            return self.queue_url
        except ClientError as e:
            logger.error(f"Error creating SQS queue: {e}")
            return None

    def get_queue_url(self) -> Optional[str]:
        """Get the URL of the configured queue."""
        if self.queue_url:
            return self.queue_url

        if not self.available:
            return None

        try:
            response = self.client.get_queue_url(QueueName=self.queue_name)
            self.queue_url = response['QueueUrl']
            return self.queue_url
        except ClientError:
            return self.create_queue()

    # ── Message Operations ────────────────────────────────────────────

    def send_message(self, event_type: str, data: Dict[str, Any]) -> bool:
        """
        Send a message to the SQS queue.

        Args:
            event_type: Type of event (STOCK_UPDATE, ALERT_CREATED, etc.).
            data: Event payload data.

        Returns:
            True on success, False on failure.

        Message format:
            {
                "event_type": "STOCK_UPDATE",
                "symbol": "AAPL",
                "price": 180,
                "timestamp": "2024-01-01T00:00:00"
            }
        """
        if not self.available:
            logger.warning("SQS unavailable — message not sent.")
            return False

        queue_url = self.get_queue_url()
        if not queue_url:
            return False

        try:
            message_body = {
                'event_type': event_type,
                'timestamp': datetime.utcnow().isoformat(),
                **data,
            }
            response = self.client.send_message(
                QueueUrl=queue_url,
                MessageBody=json.dumps(message_body, default=str),
                MessageAttributes={
                    'EventType': {
                        'StringValue': event_type,
                        'DataType': 'String',
                    },
                },
            )
            message_id = response.get('MessageId', 'unknown')
            logger.info(
                f"SQS message sent: {event_type} (ID: {message_id})"
            )
            return True
        except ClientError as e:
            logger.error(f"Error sending SQS message: {e}")
            return False

    def receive_message(self, max_messages: int = 1,
                         wait_time: int = 10) -> List[Dict[str, Any]]:
        """
        Receive messages from the SQS queue.

        Args:
            max_messages: Max number of messages to receive (1-10).
            wait_time: Long-polling wait time in seconds.

        Returns:
            List of message dictionaries with 'body', 'receipt_handle', 'message_id'.
        """
        if not self.available:
            return []

        queue_url = self.get_queue_url()
        if not queue_url:
            return []

        try:
            response = self.client.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=min(max_messages, 10),
                WaitTimeSeconds=wait_time,
                MessageAttributeNames=['All'],
            )
            messages = []
            for msg in response.get('Messages', []):
                messages.append({
                    'message_id': msg['MessageId'],
                    'receipt_handle': msg['ReceiptHandle'],
                    'body': json.loads(msg['Body']),
                    'attributes': msg.get('MessageAttributes', {}),
                })
            logger.info(f"Received {len(messages)} SQS message(s).")
            return messages
        except ClientError as e:
            logger.error(f"Error receiving SQS messages: {e}")
            return []

    def delete_message(self, receipt_handle: str) -> bool:
        """
        Delete a processed message from the SQS queue.

        Args:
            receipt_handle: The receipt handle of the message to delete.

        Returns:
            True on success, False on failure.
        """
        if not self.available:
            return False

        queue_url = self.get_queue_url()
        if not queue_url:
            return False

        try:
            self.client.delete_message(
                QueueUrl=queue_url,
                ReceiptHandle=receipt_handle,
            )
            logger.info("SQS message deleted successfully.")
            return True
        except ClientError as e:
            logger.error(f"Error deleting SQS message: {e}")
            return False

    def get_queue_attributes(self) -> Dict[str, str]:
        """Get queue attributes (message count, etc.)."""
        if not self.available:
            return {}

        queue_url = self.get_queue_url()
        if not queue_url:
            return {}

        try:
            response = self.client.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=['All'],
            )
            return response.get('Attributes', {})
        except ClientError as e:
            logger.error(f"Error getting queue attributes: {e}")
            return {}

    def purge_queue(self) -> bool:
        """Purge all messages from the queue."""
        if not self.available:
            return False

        queue_url = self.get_queue_url()
        if not queue_url:
            return False

        try:
            self.client.purge_queue(QueueUrl=queue_url)
            logger.info("SQS queue purged.")
            return True
        except ClientError as e:
            logger.error(f"Error purging queue: {e}")
            return False
