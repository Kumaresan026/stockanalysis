"""
Analytics indicators module for the Django stocks app.

Wraps the stock_event_engine library to provide Django-level
analytics with DynamoDB storage integration.
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

# stock_event_engine is installed via .ebextensions/02_packages.config container_commands
# (from the bundled stock-event-engine/ directory). Guard against import failure so
# Django can still start even if the library install step was skipped.
try:
    from stock_event_engine.indicators import StockAnalyzer
    from stock_event_engine.signals import SignalDetector
    from stock_event_engine.exceptions import (
        InsufficientDataError,
        StockDataError,
    )
    _ENGINE_AVAILABLE = True
except ImportError as _e:
    import logging as _logging
    _logging.getLogger('stocks').error(
        f"stock_event_engine not installed — analytics disabled: {_e}. "
        "Run: pip install stock-event-engine OR deploy via EB (02_packages.config installs it)."
    )
    StockAnalyzer = None
    SignalDetector = None
    InsufficientDataError = Exception
    StockDataError = Exception
    _ENGINE_AVAILABLE = False

from stocks.services.dynamodb_service import DynamoDBService
from stocks.services.cloudwatch_service import CloudWatchService

logger = logging.getLogger('stocks')


class StockIndicatorService:
    """
    Provides technical indicators and analytics for the Django application.
    Integrates with stock_event_engine and AWS services.
    """

    def __init__(self):
        self.dynamodb = DynamoDBService()
        self.cloudwatch = CloudWatchService()

    def compute_indicators(self, symbol: str,
                            prices: List[float]) -> Dict[str, Any]:
        """
        Compute all technical indicators for a stock.

        Args:
            symbol: Stock ticker symbol.
            prices: Historical closing prices.

        Returns:
            Dictionary of computed indicator values.
        """
        result = {
            'symbol': symbol,
            'computed_at': datetime.utcnow().isoformat(),
            'indicators': {},
            'signals': {},
        }

        if not _ENGINE_AVAILABLE:
            result['error'] = 'stock_event_engine library not installed on this instance.'
            return result

        try:
            analyzer = StockAnalyzer(prices, symbol)

            # Simple Moving Averages
            if len(prices) >= 20:
                sma_20 = analyzer.moving_average(20)
                result['indicators']['sma_20'] = round(float(sma_20[-1]), 4)

            if len(prices) >= 50:
                sma_50 = analyzer.moving_average(50)
                result['indicators']['sma_50'] = round(float(sma_50[-1]), 4)

            # Exponential Moving Averages
            if len(prices) >= 12:
                ema_12 = analyzer.exponential_moving_average(12)
                result['indicators']['ema_12'] = round(float(ema_12[-1]), 4)

            if len(prices) >= 26:
                ema_26 = analyzer.exponential_moving_average(26)
                result['indicators']['ema_26'] = round(float(ema_26[-1]), 4)

            # Volatility
            if len(prices) >= 2:
                vol = analyzer.volatility()
                result['indicators']['volatility'] = round(vol, 4)

            # RSI
            if len(prices) >= 15:
                rsi = analyzer.rsi(14)
                result['indicators']['rsi'] = round(float(rsi[-1]), 4)

            # Bollinger Bands
            if len(prices) >= 20:
                upper, middle, lower = analyzer.bollinger_bands(20)
                result['indicators']['bollinger_upper'] = round(float(upper[-1]), 4)
                result['indicators']['bollinger_middle'] = round(float(middle[-1]), 4)
                result['indicators']['bollinger_lower'] = round(float(lower[-1]), 4)

            # Summary
            result['indicators']['summary'] = analyzer.summary()

        except (InsufficientDataError, StockDataError) as e:
            logger.warning(f"Indicator computation limited for {symbol}: {e}")
            result['error'] = str(e)

        # Signals
        try:
            detector = SignalDetector(prices, symbol)
            signals = detector.generate_signals()
            result['signals'] = signals
        except InsufficientDataError:
            result['signals'] = {'note': 'Insufficient data for signal detection'}

        # Store in DynamoDB
        self.dynamodb.store_analytics_result(symbol, 'INDICATORS', result)

        # Log to CloudWatch
        self.cloudwatch.log_system_event(
            'ANALYTICS_COMPUTED',
            f"Indicators computed for {symbol}"
        )

        return result

    def compute_moving_averages(self, prices: List[float],
                                 windows: List[int] = None) -> Dict[str, Any]:
        """Compute multiple SMA values."""
        windows = windows or [5, 10, 20, 50]
        analyzer = StockAnalyzer(prices)
        result = {}
        for w in windows:
            try:
                sma = analyzer.moving_average(w)
                result[f'sma_{w}'] = [round(float(v), 4) for v in sma]
            except InsufficientDataError:
                result[f'sma_{w}'] = None
        return result

    def detect_trends(self, symbol: str,
                       prices: List[float]) -> Dict[str, Any]:
        """Detect market trends for a stock."""
        try:
            detector = SignalDetector(prices, symbol)
            trend = detector.detect_trend()
            breakout = detector.breakout_detection()
            return {
                'symbol': symbol,
                'trend': trend,
                'breakout': breakout,
            }
        except InsufficientDataError as e:
            return {
                'symbol': symbol,
                'error': str(e),
            }
