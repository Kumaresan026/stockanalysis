"""
CloudWatch Service — IAM role authentication via default credential chain.

No credential injection. boto3 uses the EC2 instance profile (LabRole) on EB.
All methods catch exceptions and fail gracefully — logging must NEVER crash the app.
"""

import os
import time
import logging
from typing import Optional, List

from botocore.exceptions import ClientError
from stocks.services.aws_session import get_client

logger = logging.getLogger('stocks')


class CloudWatchService:
    """
    CloudWatch Logs — uses LabRole instance profile on EB.
    Zero credential injection; boto3 handles auth automatically.
    All logging operations are fully exception-safe (best-effort only).
    """

    def __init__(self):
        """Lightweight init — no AWS calls."""
        self.log_group       = os.getenv('CLOUDWATCH_LOG_GROUP', 'stock-platform-logs')
        self._client         = None   # lazy
        self._sequence_tokens: dict  = {}

    # ── Lazy boto3 client ─────────────────────────────────────────────────────

    @property
    def client(self):
        if self._client is None:
            self._client = get_client('logs')
        return self._client

    # ── Log Group & Stream Management ─────────────────────────────────────────

    def create_log_group(self) -> bool:
        """Create the CloudWatch log group if it doesn't exist."""
        try:
            self.client.create_log_group(logGroupName=self.log_group)
            logger.info(f"CloudWatch log group '{self.log_group}' created.")
            self.client.put_retention_policy(
                logGroupName=self.log_group,
                retentionInDays=30,
            )
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceAlreadyExistsException':
                logger.info(f"Log group '{self.log_group}' already exists.")
                return True
            logger.warning(f"[CloudWatch] create_log_group failed: {e}")
            return False
        except Exception as e:
            logger.warning(f"[CloudWatch] create_log_group failed: {e}")
            return False

    def create_log_stream(self, stream_name: str) -> bool:
        """Create a log stream within the log group."""
        try:
            self.client.create_log_stream(
                logGroupName=self.log_group,
                logStreamName=stream_name,
            )
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceAlreadyExistsException':
                return True
            logger.warning(f"[CloudWatch] create_log_stream failed: {e}")
            return False
        except Exception as e:
            logger.warning(f"[CloudWatch] create_log_stream failed: {e}")
            return False

    # ── Log Events ────────────────────────────────────────────────────────────

    def put_log_events(self, stream_name: str, messages: List[str]) -> bool:
        """Write log events to a CloudWatch log stream."""
        try:
            self.create_log_group()
            self.create_log_stream(stream_name)

            log_events = [
                {'timestamp': int(time.time() * 1000), 'message': msg}
                for msg in messages
            ]

            kwargs: dict = {
                'logGroupName':  self.log_group,
                'logStreamName': stream_name,
                'logEvents':     log_events,
            }
            token = self._sequence_tokens.get(stream_name)
            if token:
                kwargs['sequenceToken'] = token

            response = self.client.put_log_events(**kwargs)
            self._sequence_tokens[stream_name] = response.get('nextSequenceToken', '')
            logger.info(f"Logged {len(messages)} event(s) to '{stream_name}'.")
            return True

        except ClientError as e:
            code = e.response['Error']['Code']
            if code in ('InvalidSequenceTokenException', 'DataAlreadyAcceptedException'):
                expected = e.response['Error'].get('expectedSequenceToken')
                if expected:
                    self._sequence_tokens[stream_name] = expected
                    return self.put_log_events(stream_name, messages)
            logger.warning(f"[CloudWatch] put_log_events ClientError: {e}")
            return False
        except Exception as e:
            logger.warning(f"[CloudWatch] put_log_events failed ({type(e).__name__}): {e}")
            return False

    # ── Convenience Logging Methods ───────────────────────────────────────────

    def log_stock_fetch(self, symbol: str, source: str, success: bool):
        """Log a stock data fetch event."""
        try:
            status  = "SUCCESS" if success else "FAILED"
            message = f"[STOCK_FETCH] {status} | Symbol: {symbol} | Source: {source}"
            self.put_log_events('stock-fetches', [message])
        except Exception:
            pass

    def log_sqs_event(self, event_type: str, symbol: str, detail: str = ""):
        """Log an SQS event."""
        try:
            message = f"[SQS_EVENT] Type: {event_type} | Symbol: {symbol} | {detail}"
            self.put_log_events('sqs-events', [message])
        except Exception:
            pass

    def log_lambda_execution(self, function_name: str, symbol: str,
                              result: str, duration_ms: int = 0):
        """Log a Lambda function execution."""
        try:
            message = (
                f"[LAMBDA_EXEC] Function: {function_name} | "
                f"Symbol: {symbol} | Result: {result} | "
                f"Duration: {duration_ms}ms"
            )
            self.put_log_events('lambda-executions', [message])
        except Exception:
            pass

    def log_alert_trigger(self, symbol: str, condition: str,
                          threshold: float, price: float):
        """Log an alert trigger event."""
        try:
            message = (
                f"[ALERT_TRIGGER] Symbol: {symbol} | "
                f"Condition: {condition} | Threshold: {threshold} | "
                f"Price: {price}"
            )
            self.put_log_events('alert-triggers', [message])
        except Exception:
            pass

    def log_system_event(self, event: str, detail: str = ""):
        """Log a general system event."""
        try:
            message = f"[SYSTEM] {event} | {detail}"
            self.put_log_events('system-events', [message])
        except Exception:
            pass
