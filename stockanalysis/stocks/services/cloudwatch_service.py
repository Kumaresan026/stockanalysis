"""
CloudWatch Service — boto3 integration for Amazon CloudWatch Logs.

Logs all system activities: stock fetches, SQS messages,
Lambda executions, and alert triggers.
"""

import os
import time
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from botocore.exceptions import ClientError, NoCredentialsError
from stocks.services.aws_session import get_boto3_session, check_aws_available

logger = logging.getLogger('stocks')


class CloudWatchService:
    """
    Service class for Amazon CloudWatch Logs via boto3.
    Supports AWS Academy temporary credentials via AWS_SESSION_TOKEN.
    """

    def __init__(self):
        """Initialize boto3 CloudWatch Logs client."""
        self.log_group = os.getenv('CLOUDWATCH_LOG_GROUP', 'stock-platform-logs')
        self.sequence_tokens = {}
        self.available = check_aws_available()
        if not self.available:
            logger.warning("AWS credentials not configured. CloudWatch unavailable.")
            return
        try:
            session = get_boto3_session()
            self.client = session.client('logs')
            logger.info("CloudWatch service initialized successfully.")
        except (NoCredentialsError, Exception) as e:
            self.available = False
            logger.warning(f"CloudWatch init failed: {e}")

    # ── Log Group & Stream Management ─────────────────────────────────

    def create_log_group(self) -> bool:
        """
        Create the CloudWatch log group if it doesn't exist.

        Returns:
            True on success, False on failure.
        """
        if not self.available:
            return False

        try:
            self.client.create_log_group(logGroupName=self.log_group)
            logger.info(f"CloudWatch log group '{self.log_group}' created.")

            # Set retention policy (30 days)
            self.client.put_retention_policy(
                logGroupName=self.log_group,
                retentionInDays=30,
            )
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceAlreadyExistsException':
                logger.info(f"Log group '{self.log_group}' already exists.")
                return True
            logger.error(f"Error creating log group: {e}")
            return False

    def create_log_stream(self, stream_name: str) -> bool:
        """
        Create a log stream within the log group.

        Args:
            stream_name: Name of the log stream.

        Returns:
            True on success.
        """
        if not self.available:
            return False

        try:
            self.client.create_log_stream(
                logGroupName=self.log_group,
                logStreamName=stream_name,
            )
            logger.info(f"CloudWatch log stream '{stream_name}' created.")
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceAlreadyExistsException':
                return True
            logger.error(f"Error creating log stream: {e}")
            return False

    # ── Log Events ────────────────────────────────────────────────────

    def put_log_events(self, stream_name: str,
                       messages: List[str]) -> bool:
        """
        Write log events to a CloudWatch log stream.

        Args:
            stream_name: Target log stream.
            messages: List of log message strings.

        Returns:
            True on success, False on failure.
        """
        if not self.available:
            return False

        # Ensure log group and stream exist
        self.create_log_group()
        self.create_log_stream(stream_name)

        try:
            log_events = [
                {
                    'timestamp': int(time.time() * 1000),
                    'message': msg,
                }
                for msg in messages
            ]

            kwargs = {
                'logGroupName': self.log_group,
                'logStreamName': stream_name,
                'logEvents': log_events,
            }

            # Include sequence token if available
            token = self.sequence_tokens.get(stream_name)
            if token:
                kwargs['sequenceToken'] = token

            response = self.client.put_log_events(**kwargs)
            self.sequence_tokens[stream_name] = response.get(
                'nextSequenceToken', ''
            )
            logger.info(
                f"Logged {len(messages)} event(s) to '{stream_name}'."
            )
            return True

        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code in ('InvalidSequenceTokenException',
                               'DataAlreadyAcceptedException'):
                # Retry with corrected token
                expected_token = e.response['Error'].get('expectedSequenceToken')
                if expected_token:
                    self.sequence_tokens[stream_name] = expected_token
                    return self.put_log_events(stream_name, messages)
            logger.error(f"Error putting log events: {e}")
            return False

    # ── Convenience Logging Methods ───────────────────────────────────

    def log_stock_fetch(self, symbol: str, source: str, success: bool):
        """Log a stock data fetch event."""
        status = "SUCCESS" if success else "FAILED"
        message = f"[STOCK_FETCH] {status} | Symbol: {symbol} | Source: {source}"
        self.put_log_events('stock-fetches', [message])

    def log_sqs_event(self, event_type: str, symbol: str, detail: str = ""):
        """Log an SQS event."""
        message = f"[SQS_EVENT] Type: {event_type} | Symbol: {symbol} | {detail}"
        self.put_log_events('sqs-events', [message])

    def log_lambda_execution(self, function_name: str, symbol: str,
                              result: str, duration_ms: int = 0):
        """Log a Lambda function execution."""
        message = (
            f"[LAMBDA_EXEC] Function: {function_name} | "
            f"Symbol: {symbol} | Result: {result} | "
            f"Duration: {duration_ms}ms"
        )
        self.put_log_events('lambda-executions', [message])

    def log_alert_trigger(self, symbol: str, condition: str,
                          threshold: float, price: float):
        """Log an alert trigger event."""
        message = (
            f"[ALERT_TRIGGER] Symbol: {symbol} | "
            f"Condition: {condition} | Threshold: {threshold} | "
            f"Price: {price}"
        )
        self.put_log_events('alert-triggers', [message])

    def log_system_event(self, event: str, detail: str = ""):
        """Log a general system event."""
        message = f"[SYSTEM] {event} | {detail}"
        self.put_log_events('system-events', [message])
