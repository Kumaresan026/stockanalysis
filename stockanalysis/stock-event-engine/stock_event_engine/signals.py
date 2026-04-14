"""
Signal detection module — stock_event_engine v0.1.0

Provides SignalDetector (OOP) and generate_signal() (functional API)
for detecting trend direction and price breakouts.
"""

import numpy as np
from typing import List, Dict, Any

from stock_event_engine.indicators import StockAnalyzer
from stock_event_engine.exceptions import InsufficientDataError, InvalidParameterError


class SignalDetector:
    """
    Detects trading signals from a price series.

    Args:
        prices: Historical close prices, oldest first.
        symbol: Ticker symbol.
    """

    def __init__(self, prices: List[float], symbol: str = "UNKNOWN"):
        self.analyzer = StockAnalyzer(prices, symbol)
        self.prices = self.analyzer.prices
        self.symbol = symbol

    def detect_trend(self, short_window: int = 10, long_window: int = 50) -> Dict[str, Any]:
        """Detect market trend using SMA crossover (BULLISH / BEARISH / NEUTRAL)."""
        if short_window >= long_window:
            raise InvalidParameterError(
                "short_window", short_window,
                "Short window must be less than long window."
            )
        if len(self.prices) < long_window:
            raise InsufficientDataError(required=long_window, available=len(self.prices))

        short_sma = self.analyzer.moving_average(short_window)
        long_sma = self.analyzer.moving_average(long_window)
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

    def breakout_detection(self, window: int = 20, threshold: float = 2.0) -> Dict[str, Any]:
        """Detect price breakout using Bollinger Bands."""
        if len(self.prices) < window:
            raise InsufficientDataError(required=window, available=len(self.prices))

        upper, middle, lower = self.analyzer.bollinger_bands(window, threshold)
        current_price = float(self.prices[-1])
        upper_val, lower_val, middle_val = float(upper[-1]), float(lower[-1]), float(middle[-1])

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

    def sma_crossover(self, short_window: int = 10, long_window: int = 50) -> Dict[str, Any]:
        """Detect SMA crossover signal (GOLDEN_CROSS / DEATH_CROSS / NO_CROSSOVER)."""
        if len(self.prices) < long_window + 1:
            raise InsufficientDataError(required=long_window + 1, available=len(self.prices))

        short_sma = self.analyzer.moving_average(short_window)
        long_sma = self.analyzer.moving_average(long_window)
        min_len = min(len(short_sma), len(long_sma))
        short_sma = short_sma[-min_len:]
        long_sma = long_sma[-min_len:]

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
        """Generate a comprehensive signal report (trend + breakout)."""
        result: Dict[str, Any] = {"symbol": self.symbol, "signals": []}
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


# ── Functional API ────────────────────────────────────────────────────────


def generate_signal(prices: List[float], symbol: str = "UNKNOWN") -> Dict[str, Any]:
    """
    Generate trading signals for the given price series.

    This is the recommended functional entry point. Internally creates a
    SignalDetector and returns a comprehensive signal report.

    Args:
        prices: Historical close prices, oldest first (minimum 2 data points).
        symbol: Stock ticker symbol.

    Returns:
        Dict containing:
            - symbol (str)
            - trend (dict | None)     Direction (BULLISH/BEARISH/NEUTRAL) + strength
            - breakout (dict | None)  Breakout status + band values
            - signals (list[str])     Human-readable signal summary strings

    Example:
        result = generate_signal([150.0, 152.5, 148.0, 160.0, ...], "AAPL")
        print(result["trend"]["direction"])  # "BULLISH"
    """
    detector = SignalDetector(prices, symbol)
    return detector.generate_signals()
