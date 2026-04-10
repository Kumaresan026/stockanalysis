"""
Django management command: poll_sqs

Run an infinite loop that polls SQS for messages and passes them
to the local lambda functions (stock_processor, alert_handler),
simulating an event-driven architecture on Elastic Beanstalk.
"""

import time
import json
import logging
import signal
import sys
from django.core.management.base import BaseCommand

from stocks.services.sqs_service import SQSService

# Import the lambda handlers
from stocks.lambda_functions.stock_processor import lambda_handler as stock_handler
from stocks.lambda_functions.alert_handler import lambda_handler as alert_handler

logger = logging.getLogger('django')

class Command(BaseCommand):
    help = "Continuously poll SQS and process messages locally (Elastic Beanstalk Worker)"

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting SQS Poller... Press Ctrl+C to stop."))
        
        sqs = SQSService()
        
        # Setup graceful shutdown
        self.running = True
        def handle_sigint(signum, frame):
            self.stdout.write(self.style.WARNING("\nStopping SQS Poller gracefully..."))
            self.running = False
        signal.signal(signal.SIGINT, handle_sigint)
        signal.signal(signal.SIGTERM, handle_sigint)

        # Instead of exiting if SQS is unavailable (which crashes the EB deployment), 
        # we will wait for credentials to be provided.
        queue_url = None
        while self.running and not queue_url:
            if not sqs.available:
                self.stdout.write(self.style.ERROR("SQS not available. Waiting 30s..."))
            else:
                queue_url = sqs.get_queue_url()
                if not queue_url:
                    self.stdout.write(self.style.ERROR("SQS Queue not found (bad credentials?). Waiting 30s..."))
            
            if not queue_url:
                time.sleep(30)
                # Re-initialize to pickup potentially new env vars (though systemd restart is usually needed)
                sqs = SQSService()

        if not self.running:
            return

        self.stdout.write(self.style.SUCCESS(f"Listening on queue: {queue_url}"))

        # Setup graceful shutdown
        self.running = True
        def handle_sigint(signum, frame):
            self.stdout.write(self.style.WARNING("\nStopping SQS Poller gracefully..."))
            self.running = False
        signal.signal(signal.SIGINT, handle_sigint)
        signal.signal(signal.SIGTERM, handle_sigint)

        while self.running:
            try:
                # Receive up to 5 messages with 10 seconds long polling
                messages = sqs.receive_message(max_messages=5, wait_time=10)
                
                for msg in messages:
                    receipt_handle = msg.get('receipt_handle')
                    body = msg.get('body')
                    
                    if not body:
                        self.stdout.write("Skipping empty message...")
                        sqs.delete_message(receipt_handle)
                        continue
                    
                    # Convert body payload to the format expected by AWS Lambda (Records wrapping stringified JSON body)
                    event_payload = {
                        "Records": [
                            {
                                "body": json.dumps(body) if isinstance(body, dict) else body,
                                "messageId": msg.get('message_id')
                            }
                        ]
                    }

                    self.stdout.write(f"Processing message: {msg.get('message_id')}")

                    # 1. Process Stock Data and Analytics
                    try:
                        stock_result = stock_handler(event_payload, None)
                        self.stdout.write(f"  stock_processor result: {stock_result['statusCode']}")
                    except Exception as e:
                        logger.error(f"stock_processor failed: {e}")
                        self.stdout.write(self.style.ERROR(f"  stock_processor error: {e}"))

                    # 2. Process Alerts
                    try:
                        alert_result = alert_handler(event_payload, None)
                        self.stdout.write(f"  alert_handler result: {alert_result['statusCode']}")
                    except Exception as e:
                        logger.error(f"alert_handler failed: {e}")
                        self.stdout.write(self.style.ERROR(f"  alert_handler error: {e}"))

                    # Delete message after successful local processing
                    if sqs.delete_message(receipt_handle):
                        self.stdout.write(self.style.SUCCESS(f"  Deleted message {msg.get('message_id')}"))
                    else:
                        self.stdout.write(self.style.WARNING(f"  Failed to delete message {msg.get('message_id')}"))

            except Exception as e:
                logger.error(f"Error in SQS Poller: {e}")
                self.stdout.write(self.style.ERROR(f"Error in SQS Poller: {e}"))
                time.sleep(5)  # Backoff before retrying

        self.stdout.write(self.style.SUCCESS("SQS Poller stopped."))
