"""
Django management command: init_aws_resources

Creates ALL required AWS resources for the Stock Platform.
Runs automatically during Elastic Beanstalk deployment via:
  .platform/hooks/postdeploy/03-init-aws.sh

Safe to run multiple times -- all operations are idempotent.

Usage:
    python manage.py init_aws_resources
    python manage.py init_aws_resources --skip-lambda
"""

import os
import sys
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create all AWS resources (DynamoDB, S3, SQS, SNS, Lambda). Idempotent."

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-lambda', action='store_true',
            help='Skip Lambda deployment (faster if only infra refresh needed).'
        )

    def handle(self, *args, **options):
        skip_lambda = options.get('skip_lambda', False)

        self.stdout.write('\n' + '=' * 60)
        self.stdout.write('  Initializing AWS Cloud Resources')
        self.stdout.write('=' * 60)

        # Verify credentials first -- fail loudly if missing
        self.stdout.write('\n[0/6] Credential Check')
        try:
            from stocks.services.aws_session import get_boto3_session, check_aws_available
            if not check_aws_available():
                self.stdout.write(self.style.ERROR(
                    '  [X] FATAL: AWS credentials are MISSING or INVALID.\n'
                    '  Cannot create any resources without valid credentials.\n\n'
                    '  Fix:\n'
                    '  1. AWS Academy -> AWS Details -> AWS CLI (copy 3 lines)\n'
                    '  2. EB Console -> Configuration -> Software -> Environment Properties\n'
                    '     Set: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN\n'
                    '  3. Click Apply, wait for restart, then re-run this command.'
                ))
                sys.exit(1)
            session  = get_boto3_session()
            identity = session.client('sts').get_caller_identity()
            self.stdout.write(self.style.SUCCESS(
                f'  [OK] Credentials valid: {identity.get("Arn", "unknown")}'
            ))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  [X] Credential error: {e}'))
            sys.exit(1)

        all_ok = True

        # -- 1. DynamoDB --------------------------------------------------
        self.stdout.write('\n[1/6] DynamoDB -- Creating tables...')
        try:
            from stocks.services.dynamodb_service import DynamoDBService
            db = DynamoDBService()
            if db.available:
                db.create_stock_tables()
                self.stdout.write(self.style.SUCCESS('  [OK] DynamoDB: all tables ready.'))
            else:
                self.stdout.write(self.style.ERROR('  [X] DynamoDB: service unavailable.'))
                all_ok = False
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  [X] DynamoDB error: {e}'))
            all_ok = False

        # -- 2. S3 --------------------------------------------------------
        self.stdout.write('\n[2/6] S3 -- Creating bucket...')
        try:
            from stocks.services.s3_service import S3Service
            s3 = S3Service()
            if s3.available:
                created = s3.create_bucket()
                if created:
                    self.stdout.write(self.style.SUCCESS(
                        f'  [OK] S3: bucket "{s3.bucket_name}" ready.'
                    ))
                    # Upload a test file to verify write access
                    import datetime
                    test_content = (
                        f'init_aws_resources test upload\n'
                        f'timestamp: {datetime.datetime.utcnow().isoformat()}\n'
                        f'bucket: {s3.bucket_name}\n'
                    ).encode('utf-8')
                    upload_ok = s3.upload_file(
                        test_content, 'diagnostics/init_test.txt', 'text/plain'
                    )
                    if upload_ok:
                        self.stdout.write(self.style.SUCCESS(
                            '  [OK] S3: test upload succeeded (write access confirmed).'
                        ))
                    else:
                        self.stdout.write(self.style.ERROR(
                            '  [X] S3: bucket exists but write FAILED. Check s3:PutObject IAM permission.'
                        ))
                        all_ok = False
                else:
                    self.stdout.write(self.style.ERROR('  [X] S3: bucket creation failed.'))
                    all_ok = False
            else:
                self.stdout.write(self.style.ERROR('  [X] S3: service unavailable.'))
                all_ok = False
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  [X] S3 error: {e}'))
            all_ok = False

        # -- 3. SQS -------------------------------------------------------
        self.stdout.write('\n[3/6] SQS -- Creating queue...')
        try:
            from stocks.services.sqs_service import SQSService
            sqs = SQSService()
            if sqs.available:
                url = sqs.create_queue()
                if url:
                    self.stdout.write(self.style.SUCCESS(
                        f'  [OK] SQS: queue "{sqs.queue_name}" ready.\n'
                        f'       URL: {url}'
                    ))
                else:
                    self.stdout.write(self.style.ERROR('  [X] SQS: queue creation failed.'))
                    all_ok = False
            else:
                self.stdout.write(self.style.ERROR('  [X] SQS: service unavailable.'))
                all_ok = False
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  [X] SQS error: {e}'))
            all_ok = False

        # -- 4. SNS -------------------------------------------------------
        self.stdout.write('\n[4/6] SNS -- Creating topic and subscribing alert email...')
        try:
            from stocks.services.sns_service import SNSService
            sns = SNSService()
            if sns.available:
                arn = sns.create_topic()
                if arn:
                    self.stdout.write(self.style.SUCCESS(
                        f'  [OK] SNS: topic "{sns.topic_name}" ready.\n'
                        f'       ARN: {arn}'
                    ))
                    # Subscribe the default alert email so notifications are delivered
                    alert_email = os.getenv('SNS_ALERT_EMAIL', 'kumaresan2126@gmail.com')
                    if alert_email:
                        sns.subscribe(alert_email)
                        self.stdout.write(self.style.SUCCESS(
                            f'  [OK] SNS: subscription requested for {alert_email}.\n'
                            f'       If unconfirmed, check inbox for AWS confirmation email.'
                        ))
                else:
                    self.stdout.write(self.style.ERROR('  [X] SNS: topic creation failed.'))
                    all_ok = False
            else:
                self.stdout.write(self.style.ERROR('  [X] SNS: service unavailable.'))
                all_ok = False
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  [X] SNS error: {e}'))
            all_ok = False

        # -- 5. CloudWatch ------------------------------------------------
        self.stdout.write('\n[5/6] CloudWatch -- Creating log group...')
        try:
            from stocks.services.cloudwatch_service import CloudWatchService
            cw = CloudWatchService()
            if cw.available:
                cw.create_log_group()
                self.stdout.write(self.style.SUCCESS('  [OK] CloudWatch: log group ready.'))
            else:
                self.stdout.write(self.style.WARNING(
                    '  [!] CloudWatch: unavailable (non-critical, continuing).'
                ))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'  [!] CloudWatch warning: {e} (non-critical)'))

        # -- 6. Lambda ----------------------------------------------------
        if skip_lambda:
            self.stdout.write('\n[6/6] Lambda -- SKIPPED (--skip-lambda flag set).')
        else:
            self.stdout.write('\n[6/6] Lambda -- Deploying functions and wiring SQS trigger...')
            try:
                from stocks.services.lambda_service import LambdaService
                lsvc = LambdaService()
                if lsvc.available:
                    queue_name = os.getenv('SQS_QUEUE_NAME', 'stock-events-queue')

                    # Deploy stock_processor
                    self.stdout.write('  Deploying stock_processor...')
                    arn1 = lsvc.deploy_function(
                        'stock_processor',
                        'stock_processor.lambda_handler',
                        'Processes SQS stock events, stores to DynamoDB/S3, chains alert_handler',
                    )
                    if arn1:
                        self.stdout.write('  Waiting for stock_processor to become Active...')
                        lsvc.wait_for_active('stock_processor')
                        self.stdout.write(self.style.SUCCESS(
                            f'  [OK] Lambda: stock_processor deployed.\n'
                            f'       ARN: {arn1}'
                        ))
                    else:
                        self.stdout.write(self.style.ERROR('  [X] Lambda: stock_processor deployment FAILED.'))
                        all_ok = False

                    # Deploy alert_handler
                    self.stdout.write('  Deploying alert_handler...')
                    arn2 = lsvc.deploy_function(
                        'alert_handler',
                        'alert_handler.lambda_handler',
                        'Evaluates alert rules and sends SNS notifications',
                    )
                    if arn2:
                        self.stdout.write('  Waiting for alert_handler to become Active...')
                        lsvc.wait_for_active('alert_handler')
                        self.stdout.write(self.style.SUCCESS(
                            f'  [OK] Lambda: alert_handler deployed.\n'
                            f'       ARN: {arn2}'
                        ))
                    else:
                        self.stdout.write(self.style.ERROR('  [X] Lambda: alert_handler deployment FAILED.'))
                        all_ok = False

                    # Wire SQS -> stock_processor trigger
                    if arn1:
                        self.stdout.write(f'  Creating SQS trigger: {queue_name} -> stock_processor...')
                        ok = lsvc.create_sqs_trigger('stock_processor', queue_name)
                        if ok:
                            self.stdout.write(self.style.SUCCESS(
                                f'  [OK] SQS trigger active: {queue_name} -> stock_processor.\n'
                                '       Every SQS message automatically invokes stock_processor Lambda.'
                            ))
                        else:
                            self.stdout.write(self.style.ERROR(
                                '  [X] SQS trigger FAILED. Check LabRole permissions for\n'
                                '      lambda:CreateEventSourceMapping and sqs:GetQueueAttributes.'
                            ))
                            all_ok = False
                else:
                    self.stdout.write(self.style.ERROR('  [X] Lambda: service unavailable.'))
                    all_ok = False
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  [X] Lambda error: {e}'))
                all_ok = False

        # -- Result -------------------------------------------------------
        self.stdout.write('\n' + '=' * 60)
        if all_ok:
            self.stdout.write(self.style.SUCCESS(
                '  [OK] ALL AWS RESOURCES INITIALIZED SUCCESSFULLY.\n'
                '\n'
                '  Event-driven pipeline: Django -> SQS -> Lambda -> DynamoDB -> SNS\n'
                '\n'
                '  Verify with: python manage.py check_aws --verbose\n'
                '  Test alerts: python manage.py test_alert --symbol AMZN --price 177\n'
            ))
        else:
            self.stdout.write(self.style.ERROR(
                '  [X] SOME RESOURCES FAILED TO INITIALIZE.\n'
                '  See errors above. Common causes:\n'
                '  - AWS credentials expired: refresh in EB Console\n'
                '  - IAM permissions: ensure LabRole has DynamoDB/S3/SQS/SNS/Lambda access\n'
                '  - Retry: python manage.py init_aws_resources\n'
            ))
            sys.exit(1)
        self.stdout.write('=' * 60 + '\n')
