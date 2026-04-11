"""
Django management command: init_aws_resources

Creates all required AWS resources for the Stock Platform.
Runs automatically during Elastic Beanstalk deployment.
Safe to run multiple times — all operations are idempotent.

Usage:
    python manage.py init_aws_resources
"""

from django.core.management.base import BaseCommand

from stocks.services.dynamodb_service import DynamoDBService
from stocks.services.s3_service import S3Service
from stocks.services.sqs_service import SQSService
from stocks.services.sns_service import SNSService
from stocks.services.cloudwatch_service import CloudWatchService
from stocks.services.lambda_service import LambdaService


class Command(BaseCommand):
    help = "Initialize all required AWS resources (DynamoDB, S3, SQS, SNS, CloudWatch, Lambda)."

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Initializing AWS Cloud Resources ===\n"))
        # Note: step count updated to 6 to include Lambda

        # ── 1. DynamoDB ──────────────────────────────────────────────
        self.stdout.write("  [1/6] DynamoDB — creating tables...")
        try:
            db = DynamoDBService()
            if db.available:
                db.create_stock_tables()
                self.stdout.write(self.style.SUCCESS("        ✓ DynamoDB tables ready."))
            else:
                self.stdout.write(self.style.WARNING("        ⚠ DynamoDB unavailable — credentials missing."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"        ✗ DynamoDB error: {e}"))

        # ── 2. S3 ────────────────────────────────────────────────────
        self.stdout.write("  [2/6] S3 — creating bucket...")
        try:
            s3 = S3Service()
            if s3.available:
                s3.create_bucket()
                self.stdout.write(self.style.SUCCESS("        ✓ S3 bucket ready."))
            else:
                self.stdout.write(self.style.WARNING("        ⚠ S3 unavailable — credentials missing."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"        ✗ S3 error: {e}"))

        # ── 3. SQS ───────────────────────────────────────────────────
        self.stdout.write("  [3/6] SQS — creating queue...")
        try:
            sqs = SQSService()
            if sqs.available:
                queue_url = sqs.create_queue()
                self.stdout.write(self.style.SUCCESS(f"        ✓ SQS queue ready: {queue_url}"))
            else:
                self.stdout.write(self.style.WARNING("        ⚠ SQS unavailable — credentials missing."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"        ✗ SQS error: {e}"))

        # ── 4. SNS ───────────────────────────────────────────────────
        self.stdout.write("  [4/6] SNS — creating topic...")
        try:
            sns = SNSService()
            if sns.available:
                arn = sns.create_topic()
                self.stdout.write(self.style.SUCCESS(f"        ✓ SNS topic ready: {arn}"))
            else:
                self.stdout.write(self.style.WARNING("        ⚠ SNS unavailable — credentials missing."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"        ✗ SNS error: {e}"))

        # ── 5. CloudWatch ────────────────────────────────────────────
        self.stdout.write("  [5/6] CloudWatch — creating log group...")
        try:
            cw = CloudWatchService()
            if cw.available:
                cw.create_log_group()
                self.stdout.write(self.style.SUCCESS("        ✓ CloudWatch log group ready."))
            else:
                self.stdout.write(self.style.WARNING("        ⚠ CloudWatch unavailable — credentials missing."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"        ✗ CloudWatch error: {e}"))

        # ── 6. Lambda ────────────────────────────────────────────────
        self.stdout.write("  [6/6] Lambda — deploying functions and SQS trigger...")
        try:
            lsvc = LambdaService()
            if lsvc.available:
                import os
                # Deploy stock_processor
                arn1 = lsvc.deploy_function(
                    'stock_processor',
                    'stock_processor.lambda_handler',
                    'Processes SQS stock events, runs analytics, stores to DynamoDB/S3',
                )
                if arn1:
                    lsvc.wait_for_active('stock_processor')
                    self.stdout.write(self.style.SUCCESS("        ✓ stock_processor Lambda deployed."))
                # Deploy alert_handler
                arn2 = lsvc.deploy_function(
                    'alert_handler',
                    'alert_handler.lambda_handler',
                    'Evaluates alert rules and sends SNS notifications',
                )
                if arn2:
                    lsvc.wait_for_active('alert_handler')
                    self.stdout.write(self.style.SUCCESS("        ✓ alert_handler Lambda deployed."))
                # Create SQS trigger
                queue_name = os.getenv('SQS_QUEUE_NAME', 'stock-events-queue')
                ok = lsvc.create_sqs_trigger('stock_processor', queue_name)
                if ok:
                    self.stdout.write(self.style.SUCCESS("        ✓ SQS → Lambda trigger configured."))
            else:
                self.stdout.write(self.style.WARNING("        ⚠ Lambda unavailable — credentials missing."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"        ✗ Lambda error: {e}"))

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== AWS Resource Initialization Complete ===\n"))
