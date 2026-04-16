"""
Event Producer — sends stock events to SQS.

All AWS calls are wrapped in try/except and never raise to callers.
CloudWatch removed — use Django logger only.
"""

import logging
from datetime import datetime

from stocks.services.sqs_service import SQSService

logger = logging.getLogger('stocks')


class StockEventProducer:
    """
    Produces stock events and sends them to SQS for processing.
    All methods return True/False — never raise exceptions.
    """

    STOCK_UPDATE       = 'STOCK_UPDATE'
    ALERT_CREATED      = 'ALERT_CREATED'
    ANALYTICS_REQUEST  = 'ANALYTICS_REQUEST'
    ALERT_EVALUATION   = 'ALERT_EVALUATION'

    def __init__(self):
        pass  # No AWS calls at init — lazy creation per method

    def _sqs(self):
        return SQSService()

    def send_stock_update(self, symbol: str, price: float,
                          volume: int = 0,
                          change_percent: float = 0.0) -> bool:
        """Send a STOCK_UPDATE event to SQS. Returns True on success."""
        try:
            success = self._sqs().send_message(self.STOCK_UPDATE, {
                'symbol':         symbol.upper(),
                'price':          price,
                'volume':         volume,
                'change_percent': change_percent,
                'timestamp':      datetime.utcnow().isoformat(),
            })
            if success:
                logger.info("[SQS] STOCK_UPDATE sent for %s @ $%.2f", symbol, price)
            else:
                logger.warning("[SQS] STOCK_UPDATE failed for %s", symbol)
            return bool(success)
        except Exception as e:
            logger.warning("[SQS] send_stock_update exception for %s: %s", symbol, e)
            return False

    def send_alert_created(self, alert_id: str, user_id: str,
                           symbol: str, condition: str,
                           threshold: float) -> bool:
        """Send an ALERT_CREATED event to SQS. Returns True on success."""
        try:
            success = self._sqs().send_message(self.ALERT_CREATED, {
                'alert_id':  alert_id,
                'user_id':   user_id,
                'symbol':    symbol.upper(),
                'condition': condition,
                'threshold': threshold,
                'timestamp': datetime.utcnow().isoformat(),
            })
            if success:
                logger.info("[SQS] ALERT_CREATED sent for %s %s", symbol, condition)
            else:
                logger.warning("[SQS] ALERT_CREATED failed for %s", symbol)
            return bool(success)
        except Exception as e:
            logger.warning("[SQS] send_alert_created exception for %s: %s", symbol, e)
            return False

    def send_analytics_request(self, symbol: str,
                                analysis_type: str = 'FULL') -> bool:
        """Request analytics processing via SQS. Returns True on success."""
        try:
            success = self._sqs().send_message(self.ANALYTICS_REQUEST, {
                'symbol':        symbol.upper(),
                'analysis_type': analysis_type,
                'timestamp':     datetime.utcnow().isoformat(),
            })
            if success:
                logger.info("[SQS] ANALYTICS_REQUEST sent for %s", symbol)
            return bool(success)
        except Exception as e:
            logger.warning("[SQS] send_analytics_request exception for %s: %s", symbol, e)
            return False

    def send_alert_evaluation(self, symbol: str, price: float) -> bool:
        """Request alert evaluation via SQS. Returns True on success."""
        try:
            success = self._sqs().send_message(self.ALERT_EVALUATION, {
                'symbol':    symbol.upper(),
                'price':     price,
                'timestamp': datetime.utcnow().isoformat(),
            })
            return bool(success)
        except Exception as e:
            logger.warning("[SQS] send_alert_evaluation exception for %s: %s", symbol, e)
            return False
