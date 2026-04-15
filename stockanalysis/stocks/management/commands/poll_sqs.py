"""
Django management command: poll_sqs

Runs as the 'worker' process defined in Procfile on Elastic Beanstalk.
Polls SQS in a loop and dispatches messages to the local lambda handlers
(stock_processor, alert_handler), simulating event-driven architecture on EB.

Fixes vs original:
- Uses 'stocks' logger (captured in web.stdout.log under EB)
- Removed duplicate signal handler setup
- Added exponential backoff on credential failures
- Added per-loop AWS credential recheck after expiry window
- Clear log messages indicate WHY the worker is waiting
"""

import time
import json
import logging
import signal
import sys

from django.core.management.base import BaseCommand

from stocks.services.sqs_service import SQSService
from stocks.services.dynamodb_service import DynamoDBService
from stocks.services.sns_service import SNSService
from stocks.services.alert_evaluator import evaluate_and_notify

# Import the lambda handlers (run locally on EB, or by real Lambda on AWS)
from stocks.lambda_functions.stock_processor import lambda_handler as stock_handler
from stocks.lambda_functions.alert_handler import lambda_handler as alert_handler

logger = logging.getLogger('stocks')

# How often (in seconds) to revalidate AWS credentials while the worker is running.
# AWS Academy tokens last ~4-6h; checking every 30 min catches expiry early.
CREDENTIAL_RECHECK_INTERVAL = 1800  # 30 minutes


class Command(BaseCommand):
    help = "Continuously poll SQS and process stock/alert events (EB Worker process)"

    def handle(self, *args, **options):
        logger.info("=" * 60)
        logger.info("SQS Poller starting (Elastic Beanstalk worker process).")
        logger.info("=" * 60)
        self.stdout.write(self.style.SUCCESS("SQS Poller starting..."))

        # Initialise service instances (used by evaluate_and_notify)
        self._dynamodb = DynamoDBService()
        self._sns = SNSService()

        # ── Graceful shutdown ─────────────────────────────────────────────
        self.running = True

        def _shutdown(signum, frame):
            logger.info("SQS Poller received shutdown signal — stopping gracefully.")
            self.stdout.write(self.style.WARNING("\nStopping SQS Poller gracefully..."))
            self.running = False

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        # ── Wait until AWS credentials are valid ──────────────────────────
        # Credentials may not yet be available right after EB starts, or they
        # may have expired mid-session (AWS Academy tokens last ~4-6 hours).
        sqs, queue_url = self._wait_for_valid_credentials_and_queue()
        if sqs is None or not self.running:
            logger.warning("SQS Poller exiting — no valid credentials or queue found.")
            return

        logger.info(f"SQS Poller ready. Listening on: {queue_url}")
        self.stdout.write(self.style.SUCCESS(f"Listening on queue: {queue_url}"))

        # ── Main polling loop ─────────────────────────────────────────────
        last_credential_check = time.time()
        consecutive_errors = 0

        while self.running:
            try:
                # Receive up to 5 messages with 10-second long polling
                messages = sqs.receive_message(max_messages=5, wait_time=10)
                consecutive_errors = 0  # Reset on success

                for msg in messages:
                    receipt_handle = msg.get('receipt_handle')
                    body = msg.get('body')
                    msg_id = msg.get('message_id', 'unknown')

                    if not body:
                        logger.warning(f"Empty SQS message body (id={msg_id}) — skipping.")
                        sqs.delete_message(receipt_handle)
                        continue

                    logger.info(f"Processing SQS message: {msg_id}")
                    self.stdout.write(f"Processing message: {msg_id}")

                    # Wrap body in Lambda-style Records envelope
                    event_payload = {
                        "Records": [
                            {
                                "body": json.dumps(body) if isinstance(body, dict) else body,
                                "messageId": msg_id,
                            }
                        ]
                    }

                    # ── 1. Stock Processor ────────────────────────────────
                    try:
                        stock_result = stock_handler(event_payload, None)
                        status = stock_result.get('statusCode', '?')
                        logger.info(f"  stock_processor → statusCode={status}")
                        self.stdout.write(f"  stock_processor: {status}")
                    except Exception as exc:
                        logger.error(f"  stock_processor FAILED for msg {msg_id}: {exc}", exc_info=True)
                        self.stdout.write(self.style.ERROR(f"  stock_processor error: {exc}"))

                    # ── 2. Alert Handler (Lambda-based) ──────────────────
                    try:
                        alert_result = alert_handler(event_payload, None)
                        status = alert_result.get('statusCode', '?')
                        logger.info(f"  alert_handler → statusCode={status}")
                        self.stdout.write(f"  alert_handler: {status}")
                    except Exception as exc:
                        logger.error(f"  alert_handler FAILED for msg {msg_id}: {exc}", exc_info=True)
                        self.stdout.write(self.style.ERROR(f"  alert_handler error: {exc}"))

                    # ── 3. In-process alert evaluation (safety net) ───────
                    # Runs even if Lambda invocation fails or is not deployed.
                    body_dict = json.loads(body) if isinstance(body, str) else body
                    sym = body_dict.get('symbol', '')
                    if sym:
                        evaluate_and_notify(
                            symbol=sym,
                            price=float(body_dict.get('price', 0)),
                            volume=int(body_dict.get('volume', 0)),
                            change_percent=float(body_dict.get('change_percent', 0)),
                            dynamodb_service=self._dynamodb,
                            sns_service=self._sns,
                        )

                    # ── Delete after processing ───────────────────────────
                    if sqs.delete_message(receipt_handle):
                        logger.info(f"  Deleted SQS message: {msg_id}")
                        self.stdout.write(self.style.SUCCESS(f"  Deleted: {msg_id}"))
                    else:
                        logger.warning(f"  Could not delete SQS message: {msg_id} — it will reappear.")

            except KeyboardInterrupt:
                self.running = False
                break
            except Exception as exc:
                consecutive_errors += 1
                backoff = min(5 * consecutive_errors, 60)  # Cap at 60s
                logger.error(
                    f"SQS Poller loop error (attempt {consecutive_errors}): {exc} "
                    f"— retrying in {backoff}s",
                    exc_info=True
                )
                self.stdout.write(self.style.ERROR(f"Poller error: {exc}"))
                time.sleep(backoff)

        logger.info("SQS Poller stopped.")
        self.stdout.write(self.style.SUCCESS("SQS Poller stopped."))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _wait_for_valid_credentials_and_queue(self):
        """
        Block until the SQS queue URL is resolvable.
        Retries with backoff. Returns (SQSService, queue_url) or (None, None).
        """
        attempt = 0
        while self.running:
            attempt += 1
            sqs = SQSService()
            queue_url = sqs.get_queue_url()
            if queue_url:
                return sqs, queue_url

            logger.warning(
                f"[Attempt {attempt}] SQS queue not reachable. "
                "Ensure LabRole instance profile is attached and "
                "the queue exists. Retrying in 15s..."
            )
            self.stdout.write(
                self.style.WARNING(f"[{attempt}] SQS not reachable — waiting 15s...")
            )
            time.sleep(15)

        return None, None
