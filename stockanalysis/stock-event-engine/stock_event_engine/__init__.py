"""
stock_event_engine v0.1.0
=========================
Event-driven stock analytics and alert engine.

A reusable library for technical analysis, signal detection,
alert evaluation, and portfolio analytics.

Public API
----------
Functional convenience API (recommended):
    from stock_event_engine.indicators import calculate_indicators
    from stock_event_engine.signals import generate_signal
    from stock_event_engine.alerts import send_alert
    from stock_event_engine.portfolio import PortfolioManager

Object-oriented API (advanced usage):
    from stock_event_engine.indicators import StockAnalyzer
    from stock_event_engine.signals import SignalDetector
    from stock_event_engine.alerts import AlertEngine, AlertRule
    from stock_event_engine.portfolio import PortfolioAnalyzer
"""

from stock_event_engine.indicators import StockAnalyzer, calculate_indicators
from stock_event_engine.signals import SignalDetector, generate_signal
from stock_event_engine.alerts import AlertEngine, AlertRule, send_alert
from stock_event_engine.portfolio import PortfolioAnalyzer, PortfolioManager
from stock_event_engine.exceptions import (
    StockEventEngineError,
    StockDataError,
    AlertEvaluationError,
    InsufficientDataError,
    InvalidParameterError,
    PortfolioError,
)

__version__ = "0.1.0"
__author__ = "Kumaresan"

__all__ = [
    # Functional API
    "calculate_indicators",
    "generate_signal",
    "send_alert",
    "PortfolioManager",
    # OOP API
    "StockAnalyzer",
    "SignalDetector",
    "AlertEngine",
    "AlertRule",
    "PortfolioAnalyzer",
    # Exceptions
    "StockEventEngineError",
    "StockDataError",
    "AlertEvaluationError",
    "InsufficientDataError",
    "InvalidParameterError",
    "PortfolioError",
]
