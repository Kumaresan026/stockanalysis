"""
Lambda Service — deploys and invokes AWS Lambda functions via boto3.

Handles:
- Packaging Lambda functions as zip files
- Creating / updating Lambda functions on AWS
- Setting up SQS event source mappings (triggers)
- Direct Lambda invocation from Django views
"""

import io
import json
import logging
import os
import time
import zipfile
from pathlib import Path
from typing import Dict, Any, Optional

import boto3
from botocore.exceptions import ClientError

from stocks.services.aws_session import get_boto3_session, check_aws_available

logger = logging.getLogger('stocks')

# Project root (3 levels up from this file)
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class LambdaService:
    """
    Service class to deploy and invoke AWS Lambda functions.
    Uses LabRole IAM role (AWS Academy standard).
    """

    def __init__(self):
        self.available = check_aws_available()
        self.region = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')
        if not self.available:
            logger.warning("AWS credentials not configured. Lambda service unavailable.")
            return
        try:
            session = get_boto3_session()
            self.client = session.client('lambda')
            self.iam_client = session.client('iam')
            self.sqs_client = session.client('sqs')
            logger.info("Lambda service initialized.")
        except Exception as e:
            self.available = False
            logger.warning(f"Lambda service init failed: {e}")

    # ── IAM Role ───────────────────────────────────────────────────────

    def get_role_arn(self) -> Optional[str]:
        """Get the ARN of the LabRole IAM role (provided by AWS Academy)."""
        try:
            response = self.iam_client.get_role(RoleName='LabRole')
            arn = response['Role']['Arn']
            logger.info(f"LabRole ARN: {arn}")
            return arn
        except ClientError as e:
            logger.error(f"Error getting LabRole ARN: {e}")
            return None

    # ── Packaging ─────────────────────────────────────────────────────

    def package_function(self, function_name: str) -> bytes:
        """
        Create a deployment zip package for a Lambda function.

        Includes:
        - The Lambda handler file (e.g. stock_processor.py)
        - The stock_event_engine library package
        """
        buffer = io.BytesIO()
        func_file = BASE_DIR / 'stocks' / 'lambda_functions' / f'{function_name}.py'
        engine_dir = BASE_DIR / 'stock_event_engine'

        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Add the Lambda handler at the root of the zip
            if func_file.exists():
                zf.write(func_file, f'{function_name}.py')
                logger.info(f"Packaged {function_name}.py")
            else:
                logger.error(f"Lambda file not found: {func_file}")

            # Add stock_event_engine package so Lambda can import it
            if engine_dir.exists():
                for py_file in engine_dir.rglob('*.py'):
                    # Preserve package structure relative to project root
                    arcname = str(py_file.relative_to(BASE_DIR)).replace('\\', '/')
                    zf.write(py_file, arcname)
                logger.info("Packaged stock_event_engine library.")

        buffer.seek(0)
        return buffer.read()

    # ── Deploy ────────────────────────────────────────────────────────

    def deploy_function(self, function_name: str, handler: str,
                        description: str = '') -> Optional[str]:
        """
        Create or update a Lambda function on AWS.

        Args:
            function_name: Name of the Lambda function (e.g. 'stock_processor')
            handler: Handler entry point (e.g. 'stock_processor.lambda_handler')
            description: Human-readable description.

        Returns:
            Function ARN on success, None on failure.
        """
        if not self.available:
            return None

        role_arn = self.get_role_arn()
        if not role_arn:
            logger.error("LabRole not found — cannot deploy Lambda.")
            return None

        zip_bytes = self.package_function(function_name)

        # Environment variables for Lambda.
        # NOTE: Do NOT include AWS_DEFAULT_REGION, AWS_ACCESS_KEY_ID,
        # AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN -- these are RESERVED keys
        # that AWS Lambda automatically provides. Trying to set them causes
        # InvalidParameterValueException on CreateFunction/UpdateFunction.
        env_vars = {
            'DYNAMODB_STOCKS_TABLE':    os.getenv('DYNAMODB_STOCKS_TABLE', 'stock_data'),
            'DYNAMODB_ALERTS_TABLE':    os.getenv('DYNAMODB_ALERTS_TABLE', 'alert_rules'),
            'DYNAMODB_ANALYTICS_TABLE': os.getenv('DYNAMODB_ANALYTICS_TABLE', 'analytics_results'),
            'S3_BUCKET_NAME':           os.getenv('S3_BUCKET_NAME', 'stock-platform-reports-2024'),
            'SNS_TOPIC_NAME':           os.getenv('SNS_TOPIC_NAME', 'stock-alerts-topic'),
            'CLOUDWATCH_LOG_GROUP':     os.getenv('CLOUDWATCH_LOG_GROUP', 'stock-platform-logs'),
            'LAMBDA_ALERT_HANDLER':     os.getenv('LAMBDA_ALERT_HANDLER', 'alert_handler'),
        }

        # Check if the function already exists
        function_exists = False
        try:
            self.client.get_function(FunctionName=function_name)
            function_exists = True
        except ClientError as e:
            if e.response['Error']['Code'] != 'ResourceNotFoundException':
                logger.error(f"Error checking Lambda {function_name}: {e}")
                return None

        if function_exists:
            # Update existing function (code first, then config)
            logger.info(f"Updating existing Lambda: {function_name}")
            try:
                self.client.update_function_code(
                    FunctionName=function_name,
                    ZipFile=zip_bytes,
                )
                # Wait for code update to complete before updating config
                # (AWS ResoureConflictException if config update starts too soon)
                time.sleep(5)
                self.client.update_function_configuration(
                    FunctionName=function_name,
                    Handler=handler,
                    Environment={'Variables': env_vars},
                    Timeout=60,
                    MemorySize=256,
                )
                logger.info(f"Lambda {function_name} updated successfully.")
            except ClientError as e:
                logger.error(f"Error updating Lambda {function_name}: {e}")
                return None
        else:
            # Create new function
            logger.info(f"Creating new Lambda: {function_name}")
            try:
                self.client.create_function(
                    FunctionName=function_name,
                    Runtime='python3.9',
                    Role=role_arn,
                    Handler=handler,
                    Code={'ZipFile': zip_bytes},
                    Description=description,
                    Timeout=60,
                    MemorySize=256,
                    Environment={'Variables': env_vars},
                )
                logger.info(f"Lambda {function_name} created successfully.")
            except ClientError as e:
                if e.response['Error']['Code'] == 'ResourceConflictException':
                    # Race condition: function was created between our check and create
                    # Switch to update path
                    logger.warning(f"Lambda {function_name} already exists (race condition) - switching to update.")
                    try:
                        self.client.update_function_code(
                            FunctionName=function_name,
                            ZipFile=zip_bytes,
                        )
                        time.sleep(5)
                        self.client.update_function_configuration(
                            FunctionName=function_name,
                            Handler=handler,
                            Environment={'Variables': env_vars},
                            Timeout=60,
                            MemorySize=256,
                        )
                        logger.info(f"Lambda {function_name} updated (via conflict recovery).")
                    except ClientError as ue:
                        logger.error(f"Error updating {function_name} after conflict: {ue}")
                        return None
                else:
                    logger.error(f"Error creating Lambda {function_name}: {e}")
                    return None

        # Return function ARN
        try:
            response = self.client.get_function(FunctionName=function_name)
            arn = response['Configuration']['FunctionArn']
            return arn
        except ClientError:
            return None

    def wait_for_active(self, function_name: str, max_wait: int = 45) -> bool:
        """Poll until Lambda function state is 'Active'."""
        for _ in range(max_wait):
            try:
                resp = self.client.get_function(FunctionName=function_name)
                state = resp['Configuration'].get('State', 'Unknown')
                if state == 'Active':
                    return True
            except ClientError:
                pass
            time.sleep(1)
        logger.warning(f"{function_name} did not become Active within {max_wait}s.")
        return False

    # ── SQS Trigger ───────────────────────────────────────────────────

    def get_queue_arn(self, queue_name: str) -> Optional[str]:
        """Get the ARN of an SQS queue by name."""
        try:
            url_resp = self.sqs_client.get_queue_url(QueueName=queue_name)
            attr_resp = self.sqs_client.get_queue_attributes(
                QueueUrl=url_resp['QueueUrl'],
                AttributeNames=['QueueArn'],
            )
            return attr_resp['Attributes']['QueueArn']
        except ClientError as e:
            logger.error(f"Error getting SQS queue ARN for {queue_name}: {e}")
            return None

    def create_sqs_trigger(self, function_name: str, queue_name: str) -> bool:
        """
        Create an SQS → Lambda event source mapping (trigger).
        Idempotent — skips creation if trigger already exists.

        Args:
            function_name: Lambda function to trigger.
            queue_name: SQS queue name.

        Returns:
            True on success or if already exists.
            False if any error occurs (caller should fall back to Django poller).
        """
        if not self.available:
            return False

        queue_arn = self.get_queue_arn(queue_name)
        if not queue_arn:
            logger.error(f"[SQS] [TRIGGER_CHECK] [ERROR] queue={queue_name} not found.")
            return False

        # Idempotency: check if trigger already exists before creating
        try:
            mappings = self.client.list_event_source_mappings(
                EventSourceArn=queue_arn,
                FunctionName=function_name,
            )
            existing = mappings.get('EventSourceMappings', [])
            if existing:
                state = existing[0].get('State', 'Unknown')
                logger.info(
                    f"[SQS] [TRIGGER_CHECK] [EXISTS] "
                    f"queue={queue_name} function={function_name} state={state}"
                )
                return True
        except ClientError as e:
            code = e.response.get('Error', {}).get('Code', '')
            if code in ('AccessDenied', 'AccessDeniedException'):
                logger.warning(
                    "[Lambda] [TRIGGER_CHECK] [ACCESS_DENIED] "
                    "Falling back to poller due to IAM restriction. "
                    "The Django poll_sqs worker process will handle SQS messages instead. "
                    "This is expected in AWS Learner Academy environments."
                )
                return False
            logger.warning(f"[SQS] [TRIGGER_CHECK] [WARN] {e}")

        # Create the event source mapping
        try:
            self.client.create_event_source_mapping(
                EventSourceArn=queue_arn,
                FunctionName=function_name,
                BatchSize=5,
                FunctionResponseTypes=['ReportBatchItemFailures'],
            )
            logger.info(
                f"[SQS] [TRIGGER_CREATED] [OK] "
                f"queue={queue_name} function={function_name}"
            )
            return True
        except ClientError as e:
            code = e.response.get('Error', {}).get('Code', '')
            if code in ('AccessDenied', 'AccessDeniedException'):
                logger.warning(
                    "[Lambda] [TRIGGER_CREATE] [ACCESS_DENIED] "
                    "Falling back to poller due to IAM restriction. "
                    "The Django poll_sqs management command will process SQS messages. "
                    "Start fallback with: python manage.py poll_sqs"
                )
            else:
                logger.error(
                    f"[SQS] [TRIGGER_CREATE] [ERROR] "
                    f"queue={queue_name} function={function_name} error={e}"
                )
            return False

    # ── Direct Invocation ─────────────────────────────────────────────

    def invoke_function(self, function_name: str, payload: Dict[str, Any],
                        invocation_type: str = 'Event') -> Optional[Dict]:
        """
        Invoke a Lambda function directly via boto3.

        Args:
            function_name: Lambda function name or ARN.
            payload: Event dict to pass to the handler.
            invocation_type: 'Event' (async fire-and-forget) or
                             'RequestResponse' (synchronous).

        Returns:
            boto3 response dict, or None on failure.
        """
        if not self.available:
            return None

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
            logger.error(f"Error invoking Lambda '{function_name}': {e}")
            return None

    def invoke_stock_processor(self, symbol: str, price: float,
                               volume: int, change_percent: float) -> None:
        """
        Asynchronously invoke stock_processor Lambda with a STOCK_UPDATE event.
        Fire-and-forget — does not block the web request.
        """
        payload = {
            'Records': [{
                'body': json.dumps({
                    'event_type': 'STOCK_UPDATE',
                    'symbol': symbol,
                    'price': price,
                    'volume': volume,
                    'change_percent': change_percent,
                })
            }]
        }
        self.invoke_function(
            os.getenv('LAMBDA_STOCK_PROCESSOR', 'stock_processor'),
            payload,
            invocation_type='Event',
        )

    def invoke_alert_handler(self, symbol: str, price: float,
                             volume: int, change_percent: float) -> None:
        """
        Asynchronously invoke alert_handler Lambda to evaluate active alerts.
        Fire-and-forget — does not block the web request.
        """
        payload = {
            'symbol': symbol,
            'price': price,
            'volume': volume,
            'change_percent': change_percent,
        }
        self.invoke_function(
            os.getenv('LAMBDA_ALERT_HANDLER', 'alert_handler'),
            payload,
            invocation_type='Event',
        )
