"""
Signal detection module for stock_event_engine.

Provides trend detection and breakout identification
using technical analysis heuristics.
"""

import numpy as np
from typing import List, Dict, Any
from stock_event_engine.indicators import StockAnalyzer
from stock_event_engine.exceptions import InsufficientDataError, InvalidParameterError


class SignalDetector:
    """
    Detects trading signals from price data.

    Attributes:
        analyzer (StockAnalyzer): Underlying technical analyzer.
    """

    def __init__(self, prices: List[float], symbol: str = "UNKNOWN"):
        self.analyzer = StockAnalyzer(prices, symbol)
        self.prices = self.analyzer.prices
        self.symbol = symbol

    # ── Trend Detection ───────────────────────────────────────────────
    def detect_trend(self, short_window: int = 10, long_window: int = 50) -> Dict[str, Any]:
        """
        Detect the current market trend using SMA crossover.

        - BULLISH: short SMA > long SMA
        - BEARISH: short SMA < long SMA
        - NEUTRAL: roughly equal

        Args:
            short_window: Short-term SMA window.
            long_window:  Long-term SMA window.

        Returns:
            Dictionary with trend direction, strength, and SMA values.
        """
        if short_window >= long_window:
            raise InvalidParameterError(
                "short_window", short_window,
                "Short window must be less than long window."
            )
        if len(self.prices) < long_window:
            raise InsufficientDataError(required=long_window, available=len(self.prices))

        short_sma = self.analyzer.moving_average(short_window)
        long_sma = self.analyzer.moving_average(long_window)

        # Compare the latest values (align arrays)
        short_latest = float(short_sma[-1])
        long_latest = float(long_sma[-1])

        diff_pct = ((short_latest - long_latest) / long_latest) * 100

        if diff_pct > 1.0:
            direction = "BULLISH"
        elif diff_pct < -1.0:
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"

        return {
            "symbol": self.symbol,
            "direction": direction,
            "strength": round(abs(diff_pct), 4),
            "short_sma": round(short_latest, 4),
            "long_sma": round(long_latest, 4),
            "diff_percent": round(diff_pct, 4),
        }

    # ── Breakout Detection ────────────────────────────────────────────
    def breakout_detection(self, window: int = 20, threshold: float = 2.0) -> Dict[str, Any]:
        """
        Detect price breakout using Bollinger Bands.

        A breakout occurs when the current price is beyond the bands.

        Args:
            window: Bollinger Band window.
            threshold: Number of standard deviations.

        Returns:
            Dictionary with breakout status and direction.
        """
        if len(self.prices) < window:
            raise InsufficientDataError(required=window, available=len(self.prices))

        upper, middle, lower = self.analyzer.bollinger_bands(window, threshold)
        current_price = float(self.prices[-1])
        upper_val = float(upper[-1])
        lower_val = float(lower[-1])
        middle_val = float(middle[-1])

        if current_price > upper_val:
            status = "BREAKOUT_UP"
        elif current_price < lower_val:
            status = "BREAKOUT_DOWN"
        else:
            status = "NO_BREAKOUT"

        return {
            "symbol": self.symbol,
            "status": status,
            "current_price": round(current_price, 4),
            "upper_band": round(upper_val, 4),
            "lower_band": round(lower_val, 4),
            "middle_band": round(middle_val, 4),
        }

    # ── SMA Crossover Signal ─────────────────────────────────────────
    def sma_crossover(self, short_window: int = 10, long_window: int = 50) -> Dict[str, Any]:
        """
        Detect SMA crossover signal (golden cross / death cross).

        Returns:
            Dictionary with crossover type and relevant data.
        """
        if len(self.prices) < long_window + 1:
            raise InsufficientDataError(required=long_window + 1, available=len(self.prices))

        short_sma = self.analyzer.moving_average(short_window)
        long_sma = self.analyzer.moving_average(long_window)

        min_len = min(len(short_sma), len(long_sma))
        short_sma = short_sma[-min_len:]
        long_sma = long_sma[-min_len:]

        # Check the last two data points for a crossover
        if len(short_sma) < 2:
            return {"symbol": self.symbol, "signal": "INSUFFICIENT_DATA"}

        prev_diff = short_sma[-2] - long_sma[-2]
        curr_diff = short_sma[-1] - long_sma[-1]

        if prev_diff <= 0 and curr_diff > 0:
            signal = "GOLDEN_CROSS"
        elif prev_diff >= 0 and curr_diff < 0:
            signal = "DEATH_CROSS"
        else:
            signal = "NO_CROSSOVER"

        return {
            "symbol": self.symbol,
            "signal": signal,
            "short_sma_current": round(float(short_sma[-1]), 4),
            "long_sma_current": round(float(long_sma[-1]), 4),
        }

    def generate_signals(self) -> Dict[str, Any]:
        """Generate a comprehensive signal report."""
        result = {"symbol": self.symbol, "signals": []}

        try:
            trend = self.detect_trend()
            result["trend"] = trend
            result["signals"].append(f"Trend: {trend['direction']}")
        except InsufficientDataError:
            result["trend"] = None

        try:
            breakout = self.breakout_detection()
            result["breakout"] = breakout
            if breakout["status"] != "NO_BREAKOUT":
                result["signals"].append(f"Breakout: {breakout['status']}")
        except InsufficientDataError:
            result["breakout"] = None

        return result
