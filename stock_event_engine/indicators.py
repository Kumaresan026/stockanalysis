"""
Technical indicators module for stock_event_engine.

Provides the StockAnalyzer class with methods for computing
moving averages, exponential moving averages, and volatility.
"""

import numpy as np
from typing import List, Optional
from stock_event_engine.exceptions import (
    InsufficientDataError,
    InvalidParameterError,
    StockDataError,
)


class StockAnalyzer:
    """
    Computes technical indicators on stock price data.

    Attributes:
        prices (List[float]): Historical price data (oldest first).
        symbol (str): Stock ticker symbol.
    """

    def __init__(self, prices: List[float], symbol: str = "UNKNOWN"):
        if not prices:
            raise StockDataError(symbol=symbol, message="Price list cannot be empty.")
        self.prices = np.array(prices, dtype=float)
        self.symbol = symbol

    # ── Simple Moving Average ─────────────────────────────────────────
    def moving_average(self, window: int) -> np.ndarray:
        """
        Calculate Simple Moving Average (SMA).

        Args:
            window: Number of periods for the moving average.

        Returns:
            np.ndarray of SMA values (length = len(prices) - window + 1).

        Raises:
            InvalidParameterError: If window < 1.
            InsufficientDataError: If not enough data points.
        """
        if window < 1:
            raise InvalidParameterError("window", window, "Window must be >= 1.")
        if len(self.prices) < window:
            raise InsufficientDataError(required=window, available=len(self.prices))

        cumsum = np.cumsum(self.prices)
        cumsum[window:] = cumsum[window:] - cumsum[:-window]
        return cumsum[window - 1:] / window

    # ── Exponential Moving Average ────────────────────────────────────
    def exponential_moving_average(self, span: int) -> np.ndarray:
        """
        Calculate Exponential Moving Average (EMA).

        Args:
            span: The span (number of periods) for the EMA.

        Returns:
            np.ndarray of EMA values (same length as prices).

        Raises:
            InvalidParameterError: If span < 1.
            InsufficientDataError: If not enough data points.
        """
        if span < 1:
            raise InvalidParameterError("span", span, "Span must be >= 1.")
        if len(self.prices) < span:
            raise InsufficientDataError(required=span, available=len(self.prices))

        multiplier = 2.0 / (span + 1)
        ema = np.zeros(len(self.prices))
        # Seed EMA with the first price
        ema[0] = self.prices[0]
        for i in range(1, len(self.prices)):
            ema[i] = (self.prices[i] - ema[i - 1]) * multiplier + ema[i - 1]
        return ema

    # ── Volatility ────────────────────────────────────────────────────
    def volatility(self, window: Optional[int] = None) -> float:
        """
        Calculate the annualized volatility of returns.

        Args:
            window: Optional rolling window. If None, uses all data.

        Returns:
            Annualized volatility as a float.

        Raises:
            InsufficientDataError: If fewer than 2 data points.
        """
        data = self.prices[-window:] if window else self.prices
        if len(data) < 2:
            raise InsufficientDataError(required=2, available=len(data))

        returns = np.diff(data) / data[:-1]
        return float(np.std(returns) * np.sqrt(252))

    # ── Relative Strength Index ───────────────────────────────────────
    def rsi(self, period: int = 14) -> np.ndarray:
        """
        Calculate Relative Strength Index (RSI).

        Args:
            period: Look-back period (default 14).

        Returns:
            np.ndarray of RSI values.
        """
        if period < 1:
            raise InvalidParameterError("period", period, "RSI period must be >= 1.")
        if len(self.prices) < period + 1:
            raise InsufficientDataError(required=period + 1, available=len(self.prices))

        deltas = np.diff(self.prices)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        avg_gain = np.zeros(len(deltas))
        avg_loss = np.zeros(len(deltas))

        # Initial averages
        avg_gain[period - 1] = np.mean(gains[:period])
        avg_loss[period - 1] = np.mean(losses[:period])

        # Smoothed averages
        for i in range(period, len(deltas)):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i]) / period

        rs = np.where(avg_loss[period - 1:] != 0,
                       avg_gain[period - 1:] / avg_loss[period - 1:],
                       100.0)
        rsi_values = 100.0 - (100.0 / (1.0 + rs))
        return rsi_values

    # ── Bollinger Bands ───────────────────────────────────────────────
    def bollinger_bands(self, window: int = 20, num_std: float = 2.0):
        """
        Calculate Bollinger Bands.

        Returns:
            Tuple of (upper_band, middle_band, lower_band) as np.ndarrays.
        """
        if len(self.prices) < window:
            raise InsufficientDataError(required=window, available=len(self.prices))

        sma = self.moving_average(window)
        rolling_std = np.array([
            np.std(self.prices[i:i + window])
            for i in range(len(self.prices) - window + 1)
        ])
        upper = sma + (rolling_std * num_std)
        lower = sma - (rolling_std * num_std)
        return upper, sma, lower

    def summary(self) -> dict:
        """Return a summary dictionary of key metrics."""
        return {
            "symbol": self.symbol,
            "current_price": float(self.prices[-1]),
            "high": float(np.max(self.prices)),
            "low": float(np.min(self.prices)),
            "mean": float(np.mean(self.prices)),
            "volatility": self.volatility() if len(self.prices) >= 2 else 0.0,
            "data_points": len(self.prices),
        }
