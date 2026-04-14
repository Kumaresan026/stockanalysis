"""
Technical indicators module — stock_event_engine v0.1.0

Provides StockAnalyzer (OOP) and calculate_indicators() (functional API)
for computing moving averages, RSI, Bollinger Bands, and volatility.
"""

import numpy as np
from typing import Dict, List, Any, Optional

from stock_event_engine.exceptions import (
    InsufficientDataError,
    InvalidParameterError,
    StockDataError,
)


class StockAnalyzer:
    """
    Computes technical indicators on a list of historical stock prices.

    Args:
        prices: Historical close prices, oldest first.
        symbol: Ticker symbol (used in error messages and output).

    Example:
        analyzer = StockAnalyzer([150.0, 152.5, 151.0, 155.0], "AAPL")
        sma = analyzer.moving_average(3)
    """

    def __init__(self, prices: List[float], symbol: str = "UNKNOWN"):
        if not prices:
            raise StockDataError(symbol=symbol, message="Price list cannot be empty.")
        self.prices = np.array(prices, dtype=float)
        self.symbol = symbol

    # ── Simple Moving Average ─────────────────────────────────────────
    def moving_average(self, window: int) -> np.ndarray:
        """Calculate Simple Moving Average (SMA)."""
        if window < 1:
            raise InvalidParameterError("window", window, "Window must be >= 1.")
        if len(self.prices) < window:
            raise InsufficientDataError(required=window, available=len(self.prices))
        cumsum = np.cumsum(self.prices)
        cumsum[window:] = cumsum[window:] - cumsum[:-window]
        return cumsum[window - 1:] / window

    # ── Exponential Moving Average ────────────────────────────────────
    def exponential_moving_average(self, span: int) -> np.ndarray:
        """Calculate Exponential Moving Average (EMA)."""
        if span < 1:
            raise InvalidParameterError("span", span, "Span must be >= 1.")
        if len(self.prices) < span:
            raise InsufficientDataError(required=span, available=len(self.prices))
        multiplier = 2.0 / (span + 1)
        ema = np.zeros(len(self.prices))
        ema[0] = self.prices[0]
        for i in range(1, len(self.prices)):
            ema[i] = (self.prices[i] - ema[i - 1]) * multiplier + ema[i - 1]
        return ema

    # ── Volatility ────────────────────────────────────────────────────
    def volatility(self, window: Optional[int] = None) -> float:
        """Calculate annualised volatility of daily returns."""
        data = self.prices[-window:] if window else self.prices
        if len(data) < 2:
            raise InsufficientDataError(required=2, available=len(data))
        returns = np.diff(data) / data[:-1]
        return float(np.std(returns) * np.sqrt(252))

    # ── RSI ───────────────────────────────────────────────────────────
    def rsi(self, period: int = 14) -> np.ndarray:
        """Calculate Relative Strength Index (RSI)."""
        if period < 1:
            raise InvalidParameterError("period", period, "RSI period must be >= 1.")
        if len(self.prices) < period + 1:
            raise InsufficientDataError(required=period + 1, available=len(self.prices))
        deltas = np.diff(self.prices)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.zeros(len(deltas))
        avg_loss = np.zeros(len(deltas))
        avg_gain[period - 1] = np.mean(gains[:period])
        avg_loss[period - 1] = np.mean(losses[:period])
        for i in range(period, len(deltas)):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i]) / period
        rs = np.where(avg_loss[period - 1:] != 0,
                      avg_gain[period - 1:] / avg_loss[period - 1:], 100.0)
        return 100.0 - (100.0 / (1.0 + rs))

    # ── Bollinger Bands ───────────────────────────────────────────────
    def bollinger_bands(self, window: int = 20, num_std: float = 2.0):
        """Calculate Bollinger Bands. Returns (upper, middle, lower)."""
        if len(self.prices) < window:
            raise InsufficientDataError(required=window, available=len(self.prices))
        sma = self.moving_average(window)
        rolling_std = np.array([
            np.std(self.prices[i:i + window])
            for i in range(len(self.prices) - window + 1)
        ])
        return sma + (rolling_std * num_std), sma, sma - (rolling_std * num_std)

    def summary(self) -> Dict[str, Any]:
        """Return a summary dict of key price metrics."""
        return {
            "symbol": self.symbol,
            "current_price": float(self.prices[-1]),
            "high": float(np.max(self.prices)),
            "low": float(np.min(self.prices)),
            "mean": float(np.mean(self.prices)),
            "volatility": self.volatility() if len(self.prices) >= 2 else 0.0,
            "data_points": len(self.prices),
        }


# ── Functional API ────────────────────────────────────────────────────────


def calculate_indicators(prices: List[float], symbol: str = "UNKNOWN") -> Dict[str, Any]:
    """
    Compute a comprehensive set of technical indicators for the given price series.

    This is the recommended functional entry point. Internally creates a
    StockAnalyzer and returns a single result dict.

    Args:
        prices: Historical close prices, oldest first (minimum 2 data points).
        symbol: Stock ticker symbol used in the output dict.

    Returns:
        Dict containing:
            - symbol (str)
            - current_price (float)
            - sma_10 (float | None)      Simple Moving Average, 10-period
            - sma_50 (float | None)      Simple Moving Average, 50-period
            - ema_12 (float | None)      Exponential Moving Average, 12-period
            - rsi_14 (float | None)      RSI, 14-period
            - volatility (float)         Annualised volatility
            - bollinger_upper (float | None)
            - bollinger_lower (float | None)
            - data_points (int)

    Example:
        result = calculate_indicators([150.0, 152.5, 151.0, 155.0], "AAPL")
        print(result["current_price"])
    """
    analyzer = StockAnalyzer(prices, symbol)
    result: Dict[str, Any] = {
        "symbol": symbol,
        "current_price": float(analyzer.prices[-1]),
        "data_points": len(analyzer.prices),
        "volatility": 0.0,
        "sma_10": None,
        "sma_50": None,
        "ema_12": None,
        "rsi_14": None,
        "bollinger_upper": None,
        "bollinger_lower": None,
    }

    try:
        result["volatility"] = round(analyzer.volatility(), 4)
    except InsufficientDataError:
        pass

    try:
        sma10 = analyzer.moving_average(10)
        result["sma_10"] = round(float(sma10[-1]), 4)
    except InsufficientDataError:
        pass

    try:
        sma50 = analyzer.moving_average(50)
        result["sma_50"] = round(float(sma50[-1]), 4)
    except InsufficientDataError:
        pass

    try:
        ema12 = analyzer.exponential_moving_average(12)
        result["ema_12"] = round(float(ema12[-1]), 4)
    except InsufficientDataError:
        pass

    try:
        rsi = analyzer.rsi(14)
        result["rsi_14"] = round(float(rsi[-1]), 4)
    except InsufficientDataError:
        pass

    try:
        upper, _, lower = analyzer.bollinger_bands(20)
        result["bollinger_upper"] = round(float(upper[-1]), 4)
        result["bollinger_lower"] = round(float(lower[-1]), 4)
    except InsufficientDataError:
        pass

    return result
