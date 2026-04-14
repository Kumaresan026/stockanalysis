"""
Portfolio analytics module — stock_event_engine v0.1.0

Provides PortfolioAnalyzer (OOP) and PortfolioManager (alias) for
computing portfolio returns, risk metrics, and drawdown analysis.
"""

import numpy as np
from typing import Dict, List, Any, Optional

from stock_event_engine.exceptions import (
    InsufficientDataError,
    InvalidParameterError,
    PortfolioError,
)


class PortfolioAnalyzer:
    """
    Analyses a portfolio of stocks for return and risk metrics.

    Args:
        holdings: Optional initial holdings dict:
                  {symbol: {"prices": [float, ...], "weight": float}}

    Example:
        pa = PortfolioAnalyzer()
        pa.add_holding("AAPL", [150.0, 155.0, 160.0], weight=0.6)
        pa.add_holding("GOOGL", [2800.0, 2850.0, 2900.0], weight=0.4)
        print(pa.portfolio_return())
    """

    def __init__(self, holdings: Optional[Dict[str, Dict[str, Any]]] = None):
        self.holdings: Dict[str, Dict[str, Any]] = holdings or {}

    def add_holding(self, symbol: str, prices: List[float], weight: float = 1.0):
        """Add or update a holding in the portfolio."""
        if not prices:
            raise InvalidParameterError("prices", prices, "Price list cannot be empty.")
        if weight < 0:
            raise InvalidParameterError("weight", weight, "Weight must be >= 0.")
        self.holdings[symbol.upper()] = {
            "prices": np.array(prices, dtype=float),
            "weight": weight,
        }

    # ── Portfolio Return ──────────────────────────────────────────────
    def portfolio_return(self) -> Dict[str, Any]:
        """Calculate weighted portfolio return."""
        if not self.holdings:
            raise PortfolioError("No holdings in portfolio.")

        individual_returns: Dict[str, Any] = {}
        weighted_return = 0.0
        total_weight = sum(h["weight"] for h in self.holdings.values())

        for symbol, data in self.holdings.items():
            prices = data["prices"]
            if len(prices) < 2:
                raise InsufficientDataError(required=2, available=len(prices))
            ret = (float(prices[-1]) - float(prices[0])) / float(prices[0])
            norm_w = data["weight"] / total_weight if total_weight > 0 else 0
            individual_returns[symbol] = {
                "return": round(ret * 100, 4),
                "weight": round(norm_w, 4),
                "start_price": round(float(prices[0]), 2),
                "end_price": round(float(prices[-1]), 2),
            }
            weighted_return += ret * norm_w

        return {
            "portfolio_return_pct": round(weighted_return * 100, 4),
            "individual_returns": individual_returns,
            "num_holdings": len(self.holdings),
        }

    # ── Risk Analysis ─────────────────────────────────────────────────
    def risk_analysis(self) -> Dict[str, Any]:
        """Perform portfolio risk analysis (volatility, Sharpe ratio, drawdown)."""
        if not self.holdings:
            raise PortfolioError("No holdings in portfolio.")

        total_weight = sum(h["weight"] for h in self.holdings.values())
        stock_risks: Dict[str, Any] = {}
        portfolio_variance = 0.0

        for symbol, data in self.holdings.items():
            prices = data["prices"]
            if len(prices) < 2:
                raise InsufficientDataError(required=2, available=len(prices))
            returns = np.diff(prices) / prices[:-1]
            vol = float(np.std(returns) * np.sqrt(252))
            max_dd = self._max_drawdown(prices)
            norm_w = data["weight"] / total_weight if total_weight > 0 else 0
            stock_risks[symbol] = {
                "volatility": round(vol, 4),
                "max_drawdown_pct": round(max_dd * 100, 4),
                "avg_daily_return": round(float(np.mean(returns)) * 100, 4),
                "weight": round(norm_w, 4),
            }
            portfolio_variance += (norm_w ** 2) * (vol ** 2)

        portfolio_vol = float(np.sqrt(portfolio_variance))
        portfolio_ret = self.portfolio_return()["portfolio_return_pct"] / 100
        risk_free_rate = 0.04
        sharpe = (
            (portfolio_ret - risk_free_rate) / portfolio_vol
            if portfolio_vol > 0 else 0.0
        )

        return {
            "portfolio_volatility": round(portfolio_vol, 4),
            "sharpe_ratio": round(sharpe, 4),
            "stock_risks": stock_risks,
            "num_holdings": len(self.holdings),
        }

    @staticmethod
    def _max_drawdown(prices: np.ndarray) -> float:
        """Calculate maximum drawdown from peak."""
        peak, max_dd = prices[0], 0.0
        for price in prices:
            if price > peak:
                peak = price
            dd = (peak - price) / peak
            if dd > max_dd:
                max_dd = dd
        return float(max_dd)

    def summary(self) -> Dict[str, Any]:
        """Return a complete portfolio summary (returns + risk)."""
        try:
            return {
                "returns": self.portfolio_return(),
                "risk": self.risk_analysis(),
                "holdings": list(self.holdings.keys()),
            }
        except (PortfolioError, InsufficientDataError) as exc:
            return {"error": str(exc)}


# ── Alias: PortfolioManager → PortfolioAnalyzer ───────────────────────────
#
# PortfolioManager is the public-facing name used in the functional import
# pattern.  It is identical to PortfolioAnalyzer; both are part of the
# public API.
#
PortfolioManager = PortfolioAnalyzer
