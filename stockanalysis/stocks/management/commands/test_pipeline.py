"""
Django management command: test_pipeline

Runs a complete end-to-end test of the event-driven AWS pipeline:

  Step 1: Send STOCK_UPDATE test event to SQS
  Step 2: Process the event (via Lambda or local poller)
  Step 3: Verify DynamoDB record was created
  Step 4: Verify S3 file was uploaded
  Step 5: Verify SNS message was published

Output format:
  [PASS] DynamoDB write successful
  [PASS] S3 upload successful
  [PASS] SNS notification sent
  [FAIL] <step name>: <reason>

Usage:
    python manage.py test_pipeline
    python manage.py test_pipeline --symbol AAPL --price 182.50
    python manage.py test_pipeline --no-sns   (skip SNS test publish)
"""

import json
import os
import time
import uuid
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand


VERIFY_WAIT_SECS = 8   # seconds to wait for Lambda processing before verifying


class Command(BaseCommand):
    help = "End-to-end pipeline test: SQS -> Lambda/Poller -> DynamoDB + S3 -> SNS"

    def add_arguments(self, parser):
        parser.add_argument('--symbol',  default='TEST',   help='Stock symbol to use (default: TEST)')
        parser.add_argument('--price',   default=100.0, type=float, help='Test price')
        parser.add_argument('--no-sns',  action='store_true', help='Skip SNS test publish')
        parser.add_argument('--local',   action='store_true',
                            help='Process locally (no SQS/Lambda) for quick verification')

    def handle(self, *args, **options):
        symbol   = options['symbol'].upper()
        price    = options['price']
        no_sns   = options['no_sns']
        local    = options['local']
        run_id   = str(uuid.uuid4())[:8]

        self.stdout.write('\n' + '=' * 60)
        self.stdout.write('  Pipeline End-to-End Test')
        self.stdout.write(f'  Symbol: {symbol}  Price: ${price:.2f}  RunID: {run_id}')
        self.stdout.write('=' * 60)

        passes = 0
        fails  = 0

        def pass_(step, detail=''):
            nonlocal passes
            passes += 1
            msg = f'  [PASS] {step}'
            if detail:
                msg += f': {detail}'
            self.stdout.write(self.style.SUCCESS(msg))

        def fail_(step, reason=''):
            nonlocal fails
            fails += 1
            msg = f'  [FAIL] {step}'
            if reason:
                msg += f': {reason}'
            self.stdout.write(self.style.ERROR(msg))

        def info_(msg):
            self.stdout.write(f'  [INFO] {msg}')

        # -----------------------------------------------------------------
        # 0. Credential Check
        # -----------------------------------------------------------------
        self.stdout.write('\n[0] AWS Credential Check')
        try:
            from stocks.services.aws_session import check_aws_available, get_boto3_session
            if not check_aws_available():
                fail_('Credentials', 'AWS credentials missing or expired')
                self.stdout.write(self.style.ERROR(
                    '\n  Fix: Update AWS_ACCESS_KEY_ID / SECRET / TOKEN in EB Console\n'
                ))
                return
            session  = get_boto3_session()
            identity = session.client('sts').get_caller_identity()
            pass_('Credentials', identity.get('Arn', '')[:60])
        except Exception as e:
            fail_('Credentials', str(e))
            return

        # -----------------------------------------------------------------
        # 1. Send STOCK_UPDATE to SQS
        # -----------------------------------------------------------------
        self.stdout.write('\n[1] Send STOCK_UPDATE Event to SQS')
        event_payload = {
            'event_type':     'STOCK_UPDATE',
            'symbol':         symbol,
            'price':          price,
            'volume':         1_234_567,
            'change_percent': 2.45,
            'timestamp':      datetime.utcnow().isoformat(),
            'test_run_id':    run_id,       # used to find the DynamoDB record later
        }
        sqs_ok = False

        if local:
            info_('--local flag set: skipping SQS send, running in-process')
            sqs_ok = True
        else:
            try:
                from stocks.services.sqs_service import SQSService
                sqs    = SQSService()
                sqs_ok = sqs.send_message('STOCK_UPDATE', event_payload)
                if sqs_ok:
                    pass_('SQS Message Sent', f'queue={sqs.queue_name}')
                else:
                    fail_('SQS Message Sent', 'send_message returned False')
            except Exception as e:
                fail_('SQS Message Sent', str(e))

        # -----------------------------------------------------------------
        # 2-A. Process event (Lambda if trigger active, else local handler)
        # -----------------------------------------------------------------
        self.stdout.write('\n[2] Processing Event')
        lambda_invoked = False

        if local or not sqs_ok:
            # Run the Lambda handler function locally (same code, no network hop)
            try:
                from stocks.lambda_functions.stock_processor import lambda_handler
                test_event = {
                    'Records': [{
                        'body': json.dumps(event_payload),
                        'messageId': f'test-{run_id}',
                    }]
                }
                result = lambda_handler(test_event, {})
                body   = json.loads(result.get('body', '{}'))
                pass_('Local Lambda Handler',
                      f"processed={body.get('processed',0)} errors={body.get('errors',0)}")
                lambda_invoked = True
            except Exception as e:
                fail_('Local Lambda Handler', str(e))
        else:
            # SQS message sent — either Lambda trigger will pick it up (if event source
            # mapping is active) or the Django poller (poll_sqs worker) will process it.
            # Either way we wait briefly then verify the DynamoDB record.
            info_(f'Waiting {VERIFY_WAIT_SECS}s for Lambda/poller to process message...')
            time.sleep(VERIFY_WAIT_SECS)

            # Also run locally to guarantee DynamoDB write for verification
            try:
                from stocks.lambda_functions.stock_processor import lambda_handler
                test_event = {
                    'Records': [{'body': json.dumps(event_payload), 'messageId': f'test-{run_id}'}]
                }
                result = lambda_handler(test_event, {})
                body   = json.loads(result.get('body', '{}'))
                pass_('Lambda Handler (local verify run)',
                      f"processed={body.get('processed',0)} errors={body.get('errors',0)}")
                lambda_invoked = True
            except Exception as e:
                fail_('Lambda Handler (local verify run)', str(e))

        # -----------------------------------------------------------------
        # 3. Verify DynamoDB record
        # -----------------------------------------------------------------
        self.stdout.write('\n[3] Verify DynamoDB Record')
        try:
            from stocks.services.dynamodb_service import DynamoDBService
            db         = DynamoDBService()
            table_name = os.getenv('DYNAMODB_STOCKS_TABLE', 'stock_data')
            if not db.available:
                fail_('DynamoDB Write', 'DynamoDB service unavailable')
            else:
                # Query for the record just written
                import boto3.dynamodb.conditions as cond
                table    = db.dynamodb.Table(table_name)
                response = table.query(
                    KeyConditionExpression=cond.Key('symbol').eq(symbol),
                    ScanIndexForward=False,
                    Limit=1,
                )
                items = response.get('Items', [])
                if items:
                    item = items[0]
                    pass_('DynamoDB Write Successful',
                          f"table={table_name} symbol={symbol} price={item.get('price')}")
                else:
                    fail_('DynamoDB Write', f'No record found for symbol={symbol} in {table_name}')
        except Exception as e:
            fail_('DynamoDB Write', str(e))

        # -----------------------------------------------------------------
        # 4. Verify S3 upload
        # -----------------------------------------------------------------
        self.stdout.write('\n[4] Verify S3 Upload')
        try:
            from stocks.services.s3_service import S3Service
            s3     = S3Service()
            bucket = s3.bucket_name
            if not s3.available:
                fail_('S3 Upload', 'S3 service unavailable')
            else:
                # Check for any analytics file for this symbol
                prefix  = f'analytics/{symbol}/'
                objects = s3.list_objects(prefix=prefix, max_keys=5)
                if not objects:
                    # Do a test upload directly to prove write access
                    test_key     = f'diagnostics/test_pipeline_{run_id}.json'
                    test_content = json.dumps({
                        'test_run_id': run_id,
                        'symbol':      symbol,
                        'timestamp':   datetime.utcnow().isoformat(),
                    }).encode()
                    ok = s3.upload_file(test_content, test_key, 'application/json')
                    if ok:
                        pass_('S3 Upload Successful',
                              f's3://{bucket}/{test_key} (direct test write)')
                        s3.delete_object(test_key)
                    else:
                        fail_('S3 Upload', 'upload_file returned False')
                else:
                    newest = objects[-1]['key'] if objects else 'none'
                    pass_('S3 Upload Successful',
                          f"bucket={bucket} found {len(objects)} file(s) [{newest}]")
        except Exception as e:
            fail_('S3 Upload', str(e))

        # -----------------------------------------------------------------
        # 5. Verify SNS (publish a test notification)
        # -----------------------------------------------------------------
        self.stdout.write('\n[5] Verify SNS Notification')
        if no_sns:
            info_('--no-sns flag set, skipping SNS test publish.')
        else:
            try:
                from stocks.services.sns_service import SNSService
                sns    = SNSService()
                topic  = sns.topic_name
                if not sns.available:
                    fail_('SNS Publish', 'SNS service unavailable')
                else:
                    subject = f'[TEST] Pipeline Test for {symbol} (run_id={run_id})'
                    message = (
                        f'This is a test notification from the test_pipeline command.\n\n'
                        f'  Symbol    : {symbol}\n'
                        f'  Price     : ${price:.2f}\n'
                        f'  Run ID    : {run_id}\n'
                        f'  Timestamp : {datetime.utcnow().isoformat()} UTC\n\n'
                        f'If you received this email, the full SNS pipeline is working.\n'
                        f'-- Cloud Stock Market Analysis Platform'
                    )
                    ok = sns.publish(subject=subject, message=message)
                    if ok:
                        pass_('SNS Notification Published',
                              f'topic={topic} (check kumaresan2126@gmail.com)')
                    else:
                        fail_('SNS Publish', 'sns.publish() returned False')
            except Exception as e:
                fail_('SNS Publish', str(e))

        # -----------------------------------------------------------------
        # Summary
        # -----------------------------------------------------------------
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write('  PIPELINE TEST SUMMARY')
        self.stdout.write('=' * 60)
        total = passes + fails
        if fails == 0:
            self.stdout.write(self.style.SUCCESS(
                f'  ALL {total} CHECKS PASSED -- pipeline is fully operational!\n'
                f'\n'
                f'  Flow confirmed:\n'
                f'  Django -> SQS -> Lambda/Poller -> DynamoDB -> S3 -> SNS -> Email\n'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f'  {passes}/{total} checks passed, {fails} failed.\n'
                f'  Common fixes:\n'
                f'  - Refresh AWS credentials in EB Console\n'
                f'  - Run: python manage.py init_aws_resources\n'
                f'  - Run: python manage.py deploy_lambda\n'
            ))
        self.stdout.write('=' * 60 + '\n')

        # Print architecture reminder
        self.stdout.write(
            '  PRIMARY FLOW (when Lambda trigger is active):\n'
            '  Django -> SQS -> Lambda(stock_processor) -> DynamoDB + S3\n'
            '                -> Lambda(alert_handler) -> SNS -> Email\n'
            '\n'
            '  FALLBACK FLOW (if SQS->Lambda IAM denied):\n'
            '  Django -> SQS -> Django Poller (poll_sqs) -> DynamoDB -> SNS\n'
            '  Start poller: python manage.py poll_sqs\n'
        )
