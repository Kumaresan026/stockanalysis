"""
Custom exceptions for the stock_event_engine library.
Provides a clear exception hierarchy for error handling.
"""


class StockEventEngineError(Exception):
    """Base exception for all stock_event_engine errors."""
    pass


class StockDataError(StockEventEngineError):
    """Raised when stock data is invalid, missing, or cannot be retrieved."""

    def __init__(self, symbol: str = "", message: str = ""):
        self.symbol = symbol
        self.message = message or f"Invalid or missing data for symbol: {symbol}"
        super().__init__(self.message)


class AlertEvaluationError(StockEventEngineError):
    """Raised when an alert rule cannot be evaluated."""

    def __init__(self, rule: str = "", message: str = ""):
        self.rule = rule
        self.message = message or f"Failed to evaluate alert rule: {rule}"
        super().__init__(self.message)


class InsufficientDataError(StockEventEngineError):
    """Raised when there is not enough data to compute an indicator."""

    def __init__(self, required: int = 0, available: int = 0, message: str = ""):
        self.required = required
        self.available = available
        self.message = message or (
            f"Insufficient data: {available} data points available, "
            f"but {required} required."
        )
        super().__init__(self.message)


class InvalidParameterError(StockEventEngineError):
    """Raised when an invalid parameter is passed to an indicator or function."""

    def __init__(self, parameter: str = "", value=None, message: str = ""):
        self.parameter = parameter
        self.value = value
        self.message = message or f"Invalid parameter '{parameter}': {value}"
        super().__init__(self.message)


class PortfolioError(StockEventEngineError):
    """Raised when portfolio calculations encounter an error."""

    def __init__(self, message: str = "Portfolio calculation error"):
        self.message = message
        super().__init__(self.message)
