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
                "  ⚠ AWS credentials not configured — skipping Lambda deployment."
            ))
            return

        queue_name = os.getenv('SQS_QUEUE_NAME', 'stock-events-queue')

        # ── 1. Deploy stock_processor ──────────────────────────────────
        self.stdout.write("  [1/3] Deploying stock_processor Lambda...")
        arn = svc.deploy_function(
            'stock_processor',
            'stock_processor.lambda_handler',
            'Processes SQS stock updates: runs analytics, stores to DynamoDB/S3, chains alert_handler',
        )
        if arn:
            self.stdout.write(self.style.SUCCESS(f"        ✓ stock_processor deployed."))
            self.stdout.write("              Waiting for function to become Active...")
            active = svc.wait_for_active('stock_processor')
            if active:
                self.stdout.write(self.style.SUCCESS("        ✓ stock_processor is Active."))
            else:
                self.stdout.write(self.style.WARNING("        ⚠ stock_processor may still be pending."))
        else:
            self.stdout.write(self.style.ERROR("        ✗ stock_processor deployment failed."))
            return

        # ── 2. Deploy alert_handler ────────────────────────────────────
        self.stdout.write("  [2/3] Deploying alert_handler Lambda...")
        arn = svc.deploy_function(
            'alert_handler',
            'alert_handler.lambda_handler',
            'Evaluates alert rules from DynamoDB and sends SNS notifications',
        )
        if arn:
            self.stdout.write(self.style.SUCCESS(f"        ✓ alert_handler deployed."))
            self.stdout.write("              Waiting for function to become Active...")
            active = svc.wait_for_active('alert_handler')
            if active:
                self.stdout.write(self.style.SUCCESS("        ✓ alert_handler is Active."))
            else:
                self.stdout.write(self.style.WARNING("        ⚠ alert_handler may still be pending."))
        else:
            self.stdout.write(self.style.ERROR("        ✗ alert_handler deployment failed."))
            return

        # ── 3. Create SQS → stock_processor trigger ────────────────────
        self.stdout.write(f"  [3/3] Creating SQS trigger: {queue_name} → stock_processor...")
        ok = svc.create_sqs_trigger('stock_processor', queue_name)
        if ok:
            self.stdout.write(self.style.SUCCESS(
                f"        ✓ SQS trigger active: any message on '{queue_name}' "
                f"will automatically invoke stock_processor."
            ))
        else:
            self.stdout.write(self.style.ERROR(
                "        ✗ SQS trigger creation failed. "
                "Check that the SQS queue exists and LabRole has the right permissions."
            ))

        self.stdout.write(self.style.MIGRATE_HEADING(
            "\n=== Lambda Deployment Complete ===\n"
        ))
        self.stdout.write(
            "  Next: visit any stock page on your site to trigger the pipeline!\n"
            "  Check: AWS Lambda Console → stock_processor → Monitor → Logs in CloudWatch\n"
        )
