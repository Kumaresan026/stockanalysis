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


class Command(BaseCommand):
    help = "Initialize all required AWS resources (DynamoDB tables, S3 bucket, SQS queue, SNS topic, CloudWatch log group)."

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Initializing AWS Cloud Resources ===\n"))

        # ── 1. DynamoDB ──────────────────────────────────────────────
        self.stdout.write("  [1/5] DynamoDB — creating tables...")
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
        self.stdout.write("  [2/5] S3 — creating bucket...")
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
        self.stdout.write("  [3/5] SQS — creating queue...")
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
        self.stdout.write("  [4/5] SNS — creating topic...")
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
        self.stdout.write("  [5/5] CloudWatch — creating log group...")
        try:
            cw = CloudWatchService()
            if cw.available:
                cw.create_log_group()
                self.stdout.write(self.style.SUCCESS("        ✓ CloudWatch log group ready."))
            else:
                self.stdout.write(self.style.WARNING("        ⚠ CloudWatch unavailable — credentials missing."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"        ✗ CloudWatch error: {e}"))

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== AWS Resource Initialization Complete ===\n"))
