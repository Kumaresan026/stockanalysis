"""
Lambda Service — IAM role authentication via default credential chain.

No credential injection. boto3 uses the EC2 instance profile (LabRole) on EB.
All methods catch exceptions and fail gracefully.
"""

import io
import json
import logging
import os
import time
import zipfile
from pathlib import Path
from typing import Dict, Any, Optional

from botocore.exceptions import ClientError
from stocks.services.aws_session import get_client

logger = logging.getLogger('stocks')

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class LambdaService:
    """
    Lambda deploy / invoke — uses LabRole instance profile on EB.
    Zero credential injection; boto3 handles auth automatically.
    """

    def __init__(self):
        """Lightweight init — no AWS calls."""
        self.region  = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')
        self._client     = None   # lazy lambda client
        self._iam_client = None   # lazy iam client
        self._sqs_client = None   # lazy sqs client

    # ── Lazy boto3 clients ────────────────────────────────────────────────────

    @property
    def client(self):
        if self._client is None:
            self._client = get_client('lambda')
        return self._client

    @property
    def iam_client(self):
        if self._iam_client is None:
            self._iam_client = get_client('iam')
        return self._iam_client

    @property
    def sqs_client(self):
        if self._sqs_client is None:
            self._sqs_client = get_client('sqs')
        return self._sqs_client

    # ── IAM Role ──────────────────────────────────────────────────────────────

    def get_role_arn(self) -> Optional[str]:
        """Get the ARN of the LabRole IAM role."""
        try:
            response = self.iam_client.get_role(RoleName='LabRole')
            arn      = response['Role']['Arn']
            logger.info(f"LabRole ARN: {arn}")
            return arn
        except ClientError as e:
            logger.error(f"[Lambda] get_role_arn failed: {e}")
            return None
        except Exception as e:
            logger.warning(f"[Lambda] get_role_arn failed: {e}")
            return None

    # ── Packaging ─────────────────────────────────────────────────────────────

    def package_function(self, function_name: str) -> bytes:
        """Create a deployment zip package for a Lambda function."""
        buffer   = io.BytesIO()
        func_file  = BASE_DIR / 'stocks' / 'lambda_functions' / f'{function_name}.py'
        engine_dir = BASE_DIR / 'stock_event_engine'

        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            if func_file.exists():
                zf.write(func_file, f'{function_name}.py')
                logger.info(f"Packaged {function_name}.py")
            else:
                logger.error(f"Lambda file not found: {func_file}")

            if engine_dir.exists():
                for py_file in engine_dir.rglob('*.py'):
                    arcname = str(py_file.relative_to(BASE_DIR)).replace('\\', '/')
                    zf.write(py_file, arcname)
                logger.info("Packaged stock_event_engine library.")

        buffer.seek(0)
        return buffer.read()

    # ── Deploy ────────────────────────────────────────────────────────────────

    def deploy_function(self, function_name: str, handler: str,
                        description: str = '') -> Optional[str]:
        """
        Create or update a Lambda function on AWS.

        Returns the function ARN on success, None on failure.
        """
        role_arn = self.get_role_arn()
        if not role_arn:
            logger.error("LabRole not found — cannot deploy Lambda.")
            return None

        zip_bytes = self.package_function(function_name)

        # NOTE: Do NOT include AWS_ACCESS_KEY_ID / SECRET / TOKEN — reserved by Lambda.
        env_vars = {
            'DYNAMODB_STOCKS_TABLE':    os.getenv('DYNAMODB_STOCKS_TABLE',    'stock_data'),
            'DYNAMODB_ALERTS_TABLE':    os.getenv('DYNAMODB_ALERTS_TABLE',    'alert_rules'),
            'DYNAMODB_ANALYTICS_TABLE': os.getenv('DYNAMODB_ANALYTICS_TABLE', 'analytics_results'),
            'S3_BUCKET_NAME':           os.getenv('S3_BUCKET_NAME',           'stock-platform-reports-2024'),
            'SNS_TOPIC_NAME':           os.getenv('SNS_TOPIC_NAME',           'stock-alerts-topic'),
            'CLOUDWATCH_LOG_GROUP':     os.getenv('CLOUDWATCH_LOG_GROUP',     'stock-platform-logs'),
            'LAMBDA_ALERT_HANDLER':     os.getenv('LAMBDA_ALERT_HANDLER',     'alert_handler'),
        }

        function_exists = False
        try:
            self.client.get_function(FunctionName=function_name)
            function_exists = True
        except ClientError as e:
            if e.response['Error']['Code'] != 'ResourceNotFoundException':
                logger.error(f"[Lambda] Error checking {function_name}: {e}")
                return None

        try:
            if function_exists:
                logger.info(f"Updating existing Lambda: {function_name}")
                self.client.update_function_code(
                    FunctionName=function_name, ZipFile=zip_bytes,
                )
                time.sleep(5)
                self.client.update_function_configuration(
                    FunctionName=function_name,
                    Handler=handler,
                    Environment={'Variables': env_vars},
                    Timeout=60, MemorySize=256,
                )
                logger.info(f"Lambda {function_name} updated.")
            else:
                logger.info(f"Creating new Lambda: {function_name}")
                self.client.create_function(
                    FunctionName=function_name,
                    Runtime='python3.9',
                    Role=role_arn,
                    Handler=handler,
                    Code={'ZipFile': zip_bytes},
                    Description=description,
                    Timeout=60, MemorySize=256,
                    Environment={'Variables': env_vars},
                )
                logger.info(f"Lambda {function_name} created.")
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceConflictException':
                logger.warning(f"Lambda {function_name} conflict — retrying as update.")
                try:
                    self.client.update_function_code(
                        FunctionName=function_name, ZipFile=zip_bytes,
                    )
                    time.sleep(5)
                    self.client.update_function_configuration(
                        FunctionName=function_name, Handler=handler,
                        Environment={'Variables': env_vars},
                        Timeout=60, MemorySize=256,
                    )
                except ClientError as ue:
                    logger.error(f"[Lambda] update after conflict failed: {ue}")
                    return None
            else:
                logger.error(f"[Lambda] deploy_function failed for {function_name}: {e}")
                return None

        try:
            response = self.client.get_function(FunctionName=function_name)
            return response['Configuration']['FunctionArn']
        except Exception:
            return None

    def wait_for_active(self, function_name: str, max_wait: int = 45) -> bool:
        """Poll until Lambda function state is 'Active'."""
        for _ in range(max_wait):
            try:
                resp  = self.client.get_function(FunctionName=function_name)
                state = resp['Configuration'].get('State', 'Unknown')
                if state == 'Active':
                    return True
            except ClientError:
                pass
            time.sleep(1)
        logger.warning(f"{function_name} did not become Active within {max_wait}s.")
        return False

    # ── SQS Trigger ───────────────────────────────────────────────────────────

    def get_queue_arn(self, queue_name: str) -> Optional[str]:
        """Get the ARN of an SQS queue by name."""
        try:
            url_resp  = self.sqs_client.get_queue_url(QueueName=queue_name)
            attr_resp = self.sqs_client.get_queue_attributes(
                QueueUrl=url_resp['QueueUrl'],
                AttributeNames=['QueueArn'],
            )
            return attr_resp['Attributes']['QueueArn']
        except Exception as e:
            logger.error(f"[Lambda] get_queue_arn failed for {queue_name}: {e}")
            return None

    def create_sqs_trigger(self, function_name: str, queue_name: str) -> bool:
        """
        Create an SQS → Lambda event source mapping (idempotent).

        Returns True on success or if trigger already exists.
        """
        queue_arn = self.get_queue_arn(queue_name)
        if not queue_arn:
            logger.error(f"[Lambda] Queue {queue_name} not found — cannot create trigger.")
            return False

        try:
            mappings = self.client.list_event_source_mappings(
                EventSourceArn=queue_arn, FunctionName=function_name,
            )
            existing = mappings.get('EventSourceMappings', [])
            if existing:
                state = existing[0].get('State', 'Unknown')
                logger.info(f"[Lambda] SQS trigger already exists (state={state}).")
                return True
        except ClientError as e:
            code = e.response.get('Error', {}).get('Code', '')
            if code in ('AccessDenied', 'AccessDeniedException'):
                logger.warning(
                    "[Lambda] Access denied for list_event_source_mappings — "
                    "falling back to Django poll_sqs worker."
                )
                return False
            logger.warning(f"[Lambda] list_event_source_mappings failed: {e}")

        try:
            self.client.create_event_source_mapping(
                EventSourceArn=queue_arn,
                FunctionName=function_name,
                BatchSize=5,
                FunctionResponseTypes=['ReportBatchItemFailures'],
            )
            logger.info(f"[Lambda] SQS trigger created: {queue_name} → {function_name}")
            return True
        except ClientError as e:
            code = e.response.get('Error', {}).get('Code', '')
            if code in ('AccessDenied', 'AccessDeniedException'):
                logger.warning(
                    "[Lambda] Access denied for create_event_source_mapping — "
                    "using Django poll_sqs worker as fallback."
                )
            else:
                logger.error(f"[Lambda] create_event_source_mapping failed: {e}")
            return False

    # ── Direct Invocation ─────────────────────────────────────────────────────

    def invoke_function(self, function_name: str, payload: Dict[str, Any],
                        invocation_type: str = 'Event') -> Optional[Dict]:
        """
        Invoke a Lambda function directly.

        invocation_type: 'Event' (async) or 'RequestResponse' (sync).
        Returns boto3 response dict, or None on failure.
        """
        try:
            response = self.client.invoke(
                FunctionName=function_name,
                InvocationType=invocation_type,
                Payload=json.dumps(payload).encode('utf-8'),
            )
            logger.info(
                f"Lambda '{function_name}' invoked "
                f"(type={invocation_type}, status={response['StatusCode']})"
            )
            return response
        except ClientError as e:
            logger.error(f"[Lambda] invoke_function failed for '{function_name}': {e}")
            return None
        except Exception as e:
            logger.warning(f"[Lambda] invoke_function failed for '{function_name}': {e}")
            return None

    def invoke_stock_processor(self, symbol: str, price: float,
                               volume: int, change_percent: float) -> None:
        """Asynchronously invoke stock_processor Lambda (fire-and-forget)."""
        payload = {
            'Records': [{'body': json.dumps({
                'event_type':     'STOCK_UPDATE',
                'symbol':         symbol,
                'price':          price,
                'volume':         volume,
                'change_percent': change_percent,
            })}]
        }
        self.invoke_function(
            os.getenv('LAMBDA_STOCK_PROCESSOR', 'stock_processor'),
            payload,
            invocation_type='Event',
        )

    def invoke_alert_handler(self, symbol: str, price: float,
                             volume: int, change_percent: float) -> None:
        """Asynchronously invoke alert_handler Lambda (fire-and-forget)."""
        payload = {
            'symbol':         symbol,
            'price':          price,
            'volume':         volume,
            'change_percent': change_percent,
        }
        self.invoke_function(
            os.getenv('LAMBDA_ALERT_HANDLER', 'alert_handler'),
            payload,
            invocation_type='Event',
        )
