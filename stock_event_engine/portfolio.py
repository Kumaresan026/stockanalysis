"""
Portfolio analytics module for stock_event_engine.

Provides portfolio return calculations and risk analysis tools.
"""

import numpy as np
from typing import List, Dict, Any, Optional
from stock_event_engine.exceptions import (
    InsufficientDataError,
    InvalidParameterError,
    PortfolioError,
)


class PortfolioAnalyzer:
    """
    Analyzes a portfolio of stocks for return and risk metrics.

    Attributes:
        holdings: Dict mapping symbol → {"prices": [...], "weight": float}
    """

    def __init__(self, holdings: Optional[Dict[str, Dict[str, Any]]] = None):
        self.holdings = holdings or {}

    def add_holding(self, symbol: str, prices: List[float], weight: float = 1.0):
        """
        Add or update a holding in the portfolio.

        Args:
            symbol: Stock ticker symbol.
            prices: Historical prices.
            weight: Portfolio weight (0.0 to 1.0).
        """
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
        """
        Calculate weighted portfolio return.

        Returns:
            Dict with total return, individual returns, and weighted return.
        """
        if not self.holdings:
            raise PortfolioError("No holdings in portfolio.")

        individual_returns = {}
        weighted_return = 0.0
        total_weight = sum(h["weight"] for h in self.holdings.values())

        for symbol, data in self.holdings.items():
            prices = data["prices"]
            if len(prices) < 2:
                raise InsufficientDataError(
                    required=2, available=len(prices)
                )
            ret = (float(prices[-1]) - float(prices[0])) / float(prices[0])
            normalized_weight = data["weight"] / total_weight if total_weight > 0 else 0
            individual_returns[symbol] = {
                "return": round(ret * 100, 4),
                "weight": round(normalized_weight, 4),
                "start_price": round(float(prices[0]), 2),
                "end_price": round(float(prices[-1]), 2),
            }
            weighted_return += ret * normalized_weight

        return {
            "portfolio_return_pct": round(weighted_return * 100, 4),
            "individual_returns": individual_returns,
            "num_holdings": len(self.holdings),
        }

    # ── Risk Analysis ─────────────────────────────────────────────────
    def risk_analysis(self) -> Dict[str, Any]:
        """
        Perform portfolio risk analysis.

        Returns:
            Dict with portfolio volatility, Sharpe ratio estimate,
            max drawdown, and per-stock risk.
        """
        if not self.holdings:
            raise PortfolioError("No holdings in portfolio.")

        total_weight = sum(h["weight"] for h in self.holdings.values())
        stock_risks = {}
        portfolio_variance = 0.0

        for symbol, data in self.holdings.items():
            prices = data["prices"]
            if len(prices) < 2:
                raise InsufficientDataError(required=2, available=len(prices))

            returns = np.diff(prices) / prices[:-1]
            vol = float(np.std(returns) * np.sqrt(252))
            max_dd = self._max_drawdown(prices)
            normalized_weight = data["weight"] / total_weight if total_weight > 0 else 0

            stock_risks[symbol] = {
                "volatility": round(vol, 4),
                "max_drawdown_pct": round(max_dd * 100, 4),
                "avg_daily_return": round(float(np.mean(returns)) * 100, 4),
                "weight": round(normalized_weight, 4),
            }
            portfolio_variance += (normalized_weight ** 2) * (vol ** 2)

        portfolio_vol = float(np.sqrt(portfolio_variance))
        returns_data = self.portfolio_return()
        portfolio_ret = returns_data["portfolio_return_pct"] / 100
        risk_free_rate = 0.04  # 4% annual risk-free rate
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
        peak = prices[0]
        max_dd = 0.0
        for price in prices:
            if price > peak:
                peak = price
            dd = (peak - price) / peak
            if dd > max_dd:
                max_dd = dd
        return float(max_dd)

    def summary(self) -> Dict[str, Any]:
        """Return a complete portfolio summary."""
        try:
            returns = self.portfolio_return()
            risk = self.risk_analysis()
            return {
                "returns": returns,
                "risk": risk,
                "holdings": list(self.holdings.keys()),
            }
        except (PortfolioError, InsufficientDataError) as e:
            return {"error": str(e)}
