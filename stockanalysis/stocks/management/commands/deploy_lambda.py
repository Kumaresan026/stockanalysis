"""
Django management command: deploy_lambda

Deploys both Lambda functions to AWS and sets up the SQS trigger.

Usage:
    python manage.py deploy_lambda
"""

import os
from django.core.management.base import BaseCommand
from stocks.services.lambda_service import LambdaService


class Command(BaseCommand):
    help = "Deploy Lambda functions (stock_processor, alert_handler) to AWS and set up SQS trigger."

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING(
            "\n=== Deploying AWS Lambda Functions ===\n"
        ))

        svc = LambdaService()
        if not svc.available:
            self.stdout.write(self.style.WARNING(
                "  [!] AWS credentials not configured - skipping Lambda deployment."
            ))
            return

        queue_name = os.getenv('SQS_QUEUE_NAME', 'stock-events-queue')

        # -- 1. Deploy stock_processor ------------------------------------
        self.stdout.write("  [1/3] Deploying stock_processor Lambda...")
        arn = svc.deploy_function(
            'stock_processor',
            'stock_processor.lambda_handler',
            'Processes SQS stock updates: runs analytics, stores to DynamoDB/S3, chains alert_handler',
        )
        if arn:
            self.stdout.write(self.style.SUCCESS("  [OK] stock_processor deployed."))
            self.stdout.write("       Waiting for function to become Active...")
            active = svc.wait_for_active('stock_processor')
            if active:
                self.stdout.write(self.style.SUCCESS("  [OK] stock_processor is Active."))
            else:
                self.stdout.write(self.style.WARNING("  [!]  stock_processor may still be pending."))
        else:
            self.stdout.write(self.style.ERROR("  [X]  stock_processor deployment FAILED."))
            self.stdout.write(self.style.ERROR(
                "       Check: LabRole IAM permissions for lambda:CreateFunction\n"
                "       and that the Lambda function code file exists at:\n"
                "       stocks/lambda_functions/stock_processor.py"
            ))
            return

        # -- 2. Deploy alert_handler --------------------------------------
        self.stdout.write("  [2/3] Deploying alert_handler Lambda...")
        arn = svc.deploy_function(
            'alert_handler',
            'alert_handler.lambda_handler',
            'Evaluates alert rules from DynamoDB and sends SNS notifications',
        )
        if arn:
            self.stdout.write(self.style.SUCCESS("  [OK] alert_handler deployed."))
            self.stdout.write("       Waiting for function to become Active...")
            active = svc.wait_for_active('alert_handler')
            if active:
                self.stdout.write(self.style.SUCCESS("  [OK] alert_handler is Active."))
            else:
                self.stdout.write(self.style.WARNING("  [!]  alert_handler may still be pending."))
        else:
            self.stdout.write(self.style.ERROR("  [X]  alert_handler deployment FAILED."))
            return

        # -- 3. Create SQS -> stock_processor trigger ---------------------
        self.stdout.write(f"  [3/3] Creating SQS trigger: {queue_name} -> stock_processor...")
        ok = svc.create_sqs_trigger('stock_processor', queue_name)
        if ok:
            self.stdout.write(self.style.SUCCESS(
                f"  [OK] SQS trigger active: {queue_name} -> stock_processor\n"
                f"       Any SQS message now automatically invokes stock_processor Lambda.\n"
                f"\n"
                f"       Full event-driven flow:\n"
                f"       Django -> SQS -> Lambda(stock_processor) -> DynamoDB + S3\n"
                f"                     -> Lambda(alert_handler) -> SNS -> Email"
            ))
        else:
            self.stdout.write(self.style.ERROR(
                "  [X]  SQS trigger creation FAILED.\n"
                "       Check: LabRole has lambda:CreateEventSourceMapping permission\n"
                "       and the SQS queue exists (run: python manage.py init_aws_resources)"
            ))

        self.stdout.write(self.style.MIGRATE_HEADING(
            "\n=== Lambda Deployment Complete ===\n"
        ))
        self.stdout.write(
            "  Verify: python manage.py check_aws --verbose\n"
            "  Test:   python manage.py test_alert --symbol AMZN --price 177\n"
            "\n"
            "  To see Lambda logs:\n"
            "  AWS Console -> Lambda -> stock_processor -> Monitor -> CloudWatch Logs\n"
        )
