"""
SQS Service — IAM role authentication via default credential chain.

No credential injection. boto3 uses the EC2 instance profile (LabRole) on EB.
All methods catch exceptions and fail gracefully.
"""

import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

from botocore.exceptions import ClientError
from stocks.services.aws_session import get_client, log_aws_error

logger = logging.getLogger('stocks')


class SQSService:
    """
    SQS message queue — uses LabRole instance profile on EB.
    Zero credential injection; boto3 handles auth automatically.
    """

    def __init__(self):
        """Lightweight init — no AWS calls."""
        self.queue_name = os.getenv('SQS_QUEUE_NAME', 'stock-events-queue')
        self._client    = None   # lazy
        self._queue_url = None   # cached after first lookup

    # ── Lazy boto3 client ─────────────────────────────────────────────────────

    @property
    def client(self):
        if self._client is None:
            self._client = get_client('sqs')
        return self._client

    # ── Queue URL ─────────────────────────────────────────────────────────────

    def get_queue_url(self) -> Optional[str]:
        """Get (and cache) the URL of the configured queue."""
        if self._queue_url:
            return self._queue_url
        try:
            response = self.client.get_queue_url(QueueName=self.queue_name)
            self._queue_url = response['QueueUrl']
            return self._queue_url
        except ClientError as e:
            if e.response['Error']['Code'] == 'AWS.SimpleQueueService.NonExistentQueue':
                return self._create_queue()
            log_aws_error(e, f"sqs.get_queue_url queue={self.queue_name}")
            return None
        except Exception as e:
            logger.warning(f"[SQS] get_queue_url failed: {e}")
            return None

    def _create_queue(self) -> Optional[str]:
        """Create the SQS queue if it doesn't exist."""
        try:
            response = self.client.create_queue(
                QueueName=self.queue_name,
                Attributes={
                    'DelaySeconds':                 '0',
                    'MessageRetentionPeriod':        '345600',   # 4 days
                    'VisibilityTimeout':             '60',
                    'ReceiveMessageWaitTimeSeconds': '10',        # long polling
                },
            )
            self._queue_url = response['QueueUrl']
            logger.info(f"SQS queue created: {self._queue_url}")
            return self._queue_url
        except ClientError as e:
            log_aws_error(e, f"sqs.create_queue queue={self.queue_name}")
            return None
        except Exception as e:
            logger.warning(f"[SQS] create_queue failed: {e}")
            return None

    # ── Message Operations ─────────────────────────────────────────────────────

    def send_message(self, event_type: str, data: Dict[str, Any]) -> bool:
        """
        Send a message to the SQS queue.

        Returns True on success, False on any failure (never raises).
        """
        queue_url = self.get_queue_url()
        if not queue_url:
            logger.warning(f"[SQS] send_message skipped — queue URL unavailable.")
            return False

        try:
            message_body = {
                'event_type': event_type,
                'timestamp':  datetime.utcnow().isoformat(),
                **data,
            }
            response   = self.client.send_message(
                QueueUrl=queue_url,
                MessageBody=json.dumps(message_body, default=str),
                MessageAttributes={
                    'EventType': {
                        'StringValue': event_type,
                        'DataType':    'String',
                    },
                },
            )
            message_id = response.get('MessageId', 'unknown')
            logger.info(f"SQS message sent: {event_type} (ID: {message_id})")
            return True
        except ClientError as e:
            log_aws_error(e, f"sqs.send_message queue={self.queue_name}")
            return False
        except Exception as e:
            logger.warning(f"[SQS] send_message failed: {type(e).__name__}: {e}")
            return False

    def receive_message(self, max_messages: int = 1,
                        wait_time: int = 10) -> List[Dict[str, Any]]:
        """
        Receive messages from the SQS queue.

        Returns a list of message dicts, or [] on failure.
        """
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
                    'message_id':     msg['MessageId'],
                    'receipt_handle': msg['ReceiptHandle'],
                    'body':           json.loads(msg['Body']),
                    'attributes':     msg.get('MessageAttributes', {}),
                })
            logger.info(f"Received {len(messages)} SQS message(s).")
            return messages
        except ClientError as e:
            log_aws_error(e, f"sqs.receive_message queue={self.queue_name}")
            return []
        except Exception as e:
            logger.warning(f"[SQS] receive_message failed: {e}")
            return []

    def delete_message(self, receipt_handle: str) -> bool:
        """Delete a processed message from the SQS queue."""
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
            log_aws_error(e, "sqs.delete_message")
            return False
        except Exception as e:
            logger.warning(f"[SQS] delete_message failed: {e}")
            return False

    def get_queue_attributes(self) -> Dict[str, str]:
        """Get queue attributes (message count, etc.)."""
        queue_url = self.get_queue_url()
        if not queue_url:
            return {}

        try:
            response = self.client.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=['All'],
            )
            return response.get('Attributes', {})
        except Exception as e:
            logger.warning(f"[SQS] get_queue_attributes failed: {e}")
            return {}

    def purge_queue(self) -> bool:
        """Purge all messages from the queue."""
        queue_url = self.get_queue_url()
        if not queue_url:
            return False

        try:
            self.client.purge_queue(QueueUrl=queue_url)
            logger.info("SQS queue purged.")
            return True
        except Exception as e:
            logger.warning(f"[SQS] purge_queue failed: {e}")
            return False
