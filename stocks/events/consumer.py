"""
Event Consumer — polls SQS for stock events and processes them.

Consumes events from the SQS queue, dispatches to appropriate
handlers (analytics, alert evaluation), and logs to CloudWatch.
"""

import json
import logging
import time
from typing import Dict, Any, Callable, Optional

from stocks.services.sqs_service import SQSService
from stocks.services.cloudwatch_service import CloudWatchService
from stocks.analytics.indicators import StockIndicatorService
from stocks.api.stock_api import StockAPIService

logger = logging.getLogger('stocks')


class StockEventConsumer:
    """
    Consumes and processes stock events from SQS.
    Part of the event-driven architecture.
    """

    def __init__(self):
        self.sqs = SQSService()
        self.cloudwatch = CloudWatchService()
        self.api_service = StockAPIService()
        self.indicator_service = StockIndicatorService()

        # Event handlers registry
        self.handlers: Dict[str, Callable] = {
            'STOCK_UPDATE': self._handle_stock_update,
            'ALERT_CREATED': self._handle_alert_created,
            'ANALYTICS_REQUEST': self._handle_analytics_request,
            'ALERT_EVALUATION': self._handle_alert_evaluation,
        }

    def process_messages(self, max_messages: int = 5,
                          wait_time: int = 10) -> int:
        """
        Poll SQS and process available messages.

        Args:
            max_messages: Max messages to receive per poll.
            wait_time: Long-polling wait time.

        Returns:
            Number of messages processed.
        """
        messages = self.sqs.receive_message(max_messages, wait_time)
        processed = 0

        for msg in messages:
            try:
                body = msg['body']
                event_type = body.get('event_type', 'UNKNOWN')
                handler = self.handlers.get(event_type)

                if handler:
                    handler(body)
                    processed += 1
                    logger.info(f"Processed event: {event_type}")
                else:
                    logger.warning(f"Unknown event type: {event_type}")

                # Delete processed message
                self.sqs.delete_message(msg['receipt_handle'])

            except Exception as e:
                logger.error(f"Error processing message: {e}")
                self.cloudwatch.log_system_event(
                    'CONSUMER_ERROR', f"Error: {e}"
                )

        return processed

    def _handle_stock_update(self, event: Dict[str, Any]):
        """Handle stock update events — run analytics."""
        symbol = event.get('symbol', '')
        if not symbol:
            return

        # Fetch historical data for analytics
        history = self.api_service.get_stock_history(symbol)
        prices = [d['close'] for d in history]

        if prices:
            # Compute indicators
            self.indicator_service.compute_indicators(symbol, prices)

        self.cloudwatch.log_system_event(
            'STOCK_UPDATE_PROCESSED',
            f"Analytics computed for {symbol}"
        )

    def _handle_alert_created(self, event: Dict[str, Any]):
        """Handle alert creation events."""
        symbol = event.get('symbol', '')
        self.cloudwatch.log_system_event(
            'ALERT_CREATED_PROCESSED',
            f"Alert registered for {symbol}: "
            f"{event.get('condition')} {event.get('threshold')}"
        )

    def _handle_analytics_request(self, event: Dict[str, Any]):
        """Handle analytics request events."""
        symbol = event.get('symbol', '')
        if not symbol:
            return

        history = self.api_service.get_stock_history(symbol)
        prices = [d['close'] for d in history]

        if prices:
            self.indicator_service.compute_indicators(symbol, prices)

        self.cloudwatch.log_system_event(
            'ANALYTICS_PROCESSED',
            f"Full analytics computed for {symbol}"
        )

    def _handle_alert_evaluation(self, event: Dict[str, Any]):
        """Handle alert evaluation events."""
        from stocks.services.sns_service import SNSService
        from stock_event_engine.alerts import AlertEngine, AlertRule

        symbol = event.get('symbol', '')
        price = event.get('price', 0)

        # In production, you'd fetch alert rules from DynamoDB
        # This is a skeleton that demonstrates the flow
        self.cloudwatch.log_system_event(
            'ALERT_EVALUATION',
            f"Evaluating alerts for {symbol} at ${price}"
        )

    def run_continuous(self, poll_interval: int = 30):
        """
        Run the consumer in a continuous polling loop.

        Args:
            poll_interval: Seconds between polls.
        """
        logger.info("Starting continuous SQS consumer...")
        self.cloudwatch.log_system_event('CONSUMER_STARTED', 'Polling loop initiated')

        while True:
            try:
                processed = self.process_messages()
                if processed > 0:
                    logger.info(f"Processed {processed} messages.")
            except Exception as e:
                logger.error(f"Consumer loop error: {e}")

            time.sleep(poll_interval)
