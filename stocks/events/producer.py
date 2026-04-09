"""
Event Producer — sends stock events to SQS.

Triggers messages when:
- Stock data is updated
- User creates an alert
- Analytics processing is requested
"""

import logging
from typing import Dict, Any
from datetime import datetime

from stocks.services.sqs_service import SQSService
from stocks.services.cloudwatch_service import CloudWatchService

logger = logging.getLogger('stocks')


class StockEventProducer:
    """
    Produces stock events and sends them to SQS for processing.
    Part of the event-driven architecture.
    """

    # Event types
    STOCK_UPDATE = 'STOCK_UPDATE'
    ALERT_CREATED = 'ALERT_CREATED'
    ANALYTICS_REQUEST = 'ANALYTICS_REQUEST'
    ALERT_EVALUATION = 'ALERT_EVALUATION'

    def __init__(self):
        self.sqs = SQSService()
        self.cloudwatch = CloudWatchService()

    def send_stock_update(self, symbol: str, price: float,
                          volume: int = 0,
                          change_percent: float = 0.0) -> bool:
        """
        Send a stock update event to SQS.

        This is triggered after new stock data is fetched from the API
        and stored in DynamoDB.

        Args:
            symbol: Stock ticker symbol.
            price: Current price.
            volume: Trading volume.
            change_percent: Price change percentage.

        Returns:
            True if message was sent successfully.
        """
        data = {
            'symbol': symbol.upper(),
            'price': price,
            'volume': volume,
            'change_percent': change_percent,
        }
        success = self.sqs.send_message(self.STOCK_UPDATE, data)

        # Log to CloudWatch
        self.cloudwatch.log_sqs_event(
            self.STOCK_UPDATE, symbol,
            f"Price: ${price:.2f}, Volume: {volume}"
        )

        if success:
            logger.info(f"Stock update event sent for {symbol}")
        else:
            logger.error(f"Failed to send stock update event for {symbol}")

        return success

    def send_alert_created(self, alert_id: str, user_id: str,
                           symbol: str, condition: str,
                           threshold: float) -> bool:
        """
        Send an alert creation event to SQS.

        Args:
            alert_id: Unique alert ID.
            user_id: User who created the alert.
            symbol: Stock symbol.
            condition: Alert condition type.
            threshold: Threshold value.

        Returns:
            True on success.
        """
        data = {
            'alert_id': alert_id,
            'user_id': user_id,
            'symbol': symbol.upper(),
            'condition': condition,
            'threshold': threshold,
        }
        success = self.sqs.send_message(self.ALERT_CREATED, data)

        self.cloudwatch.log_sqs_event(
            self.ALERT_CREATED, symbol,
            f"Alert: {condition} {threshold}"
        )

        return success

    def send_analytics_request(self, symbol: str,
                                analysis_type: str = 'FULL') -> bool:
        """
        Request analytics processing for a stock.

        Args:
            symbol: Stock to analyze.
            analysis_type: Type of analysis to perform.

        Returns:
            True on success.
        """
        data = {
            'symbol': symbol.upper(),
            'analysis_type': analysis_type,
        }
        success = self.sqs.send_message(self.ANALYTICS_REQUEST, data)

        self.cloudwatch.log_sqs_event(
            self.ANALYTICS_REQUEST, symbol,
            f"Analysis type: {analysis_type}"
        )

        return success

    def send_alert_evaluation(self, symbol: str,
                               price: float) -> bool:
        """
        Request alert evaluation for a stock.

        Args:
            symbol: Stock symbol to check alerts for.
            price: Current price to evaluate against.

        Returns:
            True on success.
        """
        data = {
            'symbol': symbol.upper(),
            'price': price,
        }
        return self.sqs.send_message(self.ALERT_EVALUATION, data)
