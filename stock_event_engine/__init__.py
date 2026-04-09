"""
stock_event_engine — A reusable Python library for stock market analytics.

Provides technical indicators, signal detection, alert evaluation,
and portfolio analytics for event-driven stock processing systems.
"""

from stock_event_engine.indicators import StockAnalyzer
from stock_event_engine.signals import SignalDetector
from stock_event_engine.alerts import AlertEngine
from stock_event_engine.portfolio import PortfolioAnalyzer
from stock_event_engine.exceptions import (
    StockEventEngineError,
    StockDataError,
    AlertEvaluationError,
    InsufficientDataError,
    InvalidParameterError,
)

__version__ = "1.0.0"
__author__ = "Stock Platform Team"

__all__ = [
    "StockAnalyzer",
    "SignalDetector",
    "AlertEngine",
    "PortfolioAnalyzer",
    "StockEventEngineError",
    "StockDataError",
    "AlertEvaluationError",
    "InsufficientDataError",
    "InvalidParameterError",
]
