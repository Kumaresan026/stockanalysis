"""
Alert engine module for stock_event_engine.

Evaluates user-defined alert rules against live or cached stock data.
"""

from typing import Dict, Any, List
from stock_event_engine.exceptions import AlertEvaluationError


class AlertRule:
    """
    Represents a single alert rule.

    Attributes:
        symbol (str): Stock ticker symbol.
        condition (str): One of 'PRICE_ABOVE', 'PRICE_BELOW',
                         'VOLUME_ABOVE', 'CHANGE_ABOVE', 'CHANGE_BELOW'.
        threshold (float): The trigger threshold value.
        user_id (str): The user who created this rule.
    """

    VALID_CONDITIONS = [
        "PRICE_ABOVE",
        "PRICE_BELOW",
        "VOLUME_ABOVE",
        "CHANGE_ABOVE",
        "CHANGE_BELOW",
    ]

    def __init__(self, symbol: str, condition: str, threshold: float, user_id: str = ""):
        if condition not in self.VALID_CONDITIONS:
            raise AlertEvaluationError(
                rule=condition,
                message=f"Invalid condition '{condition}'. "
                        f"Must be one of {self.VALID_CONDITIONS}"
            )
        self.symbol = symbol.upper()
        self.condition = condition
        self.threshold = float(threshold)
        self.user_id = user_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "condition": self.condition,
            "threshold": self.threshold,
            "user_id": self.user_id,
        }


class AlertEngine:
    """
    Evaluates alert rules against current stock data.

    Usage:
        engine = AlertEngine()
        engine.add_rule(AlertRule("AAPL", "PRICE_ABOVE", 180.0, "user1"))
        triggered = engine.evaluate({"AAPL": {"price": 185.0, "volume": 1000000}})
    """

    def __init__(self):
        self.rules: List[AlertRule] = []

    def add_rule(self, rule: AlertRule):
        """Add an alert rule to the engine."""
        self.rules.append(rule)

    def remove_rules_for_user(self, user_id: str):
        """Remove all rules for a specific user."""
        self.rules = [r for r in self.rules if r.user_id != user_id]

    def evaluate_rule(self, rule: AlertRule, stock_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluate a single alert rule against stock data.

        Args:
            rule: The AlertRule to evaluate.
            stock_data: Dict with keys 'price', 'volume', 'change_percent'.

        Returns:
            Dict with evaluation result.

        Raises:
            AlertEvaluationError: If evaluation fails.
        """
        try:
            price = float(stock_data.get("price", 0))
            volume = float(stock_data.get("volume", 0))
            change_pct = float(stock_data.get("change_percent", 0))

            triggered = False
            message = ""

            if rule.condition == "PRICE_ABOVE" and price > rule.threshold:
                triggered = True
                message = (
                    f"{rule.symbol} price ${price:.2f} crossed above "
                    f"threshold ${rule.threshold:.2f}"
                )
            elif rule.condition == "PRICE_BELOW" and price < rule.threshold:
                triggered = True
                message = (
                    f"{rule.symbol} price ${price:.2f} dropped below "
                    f"threshold ${rule.threshold:.2f}"
                )
            elif rule.condition == "VOLUME_ABOVE" and volume > rule.threshold:
                triggered = True
                message = (
                    f"{rule.symbol} volume {volume:,.0f} exceeded "
                    f"threshold {rule.threshold:,.0f}"
                )
            elif rule.condition == "CHANGE_ABOVE" and change_pct > rule.threshold:
                triggered = True
                message = (
                    f"{rule.symbol} change {change_pct:.2f}% exceeded "
                    f"threshold {rule.threshold:.2f}%"
                )
            elif rule.condition == "CHANGE_BELOW" and change_pct < -rule.threshold:
                triggered = True
                message = (
                    f"{rule.symbol} change {change_pct:.2f}% dropped below "
                    f"-{rule.threshold:.2f}%"
                )

            return {
                "rule": rule.to_dict(),
                "triggered": triggered,
                "message": message,
                "current_price": price,
                "current_volume": volume,
            }

        except (ValueError, TypeError) as e:
            raise AlertEvaluationError(
                rule=str(rule.to_dict()),
                message=f"Error evaluating rule: {e}"
            )

    def evaluate(self, all_stock_data: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Evaluate all rules against current stock data.

        Args:
            all_stock_data: Dict keyed by symbol, e.g.
                {"AAPL": {"price": 180, "volume": 1000000, "change_percent": 1.5}}

        Returns:
            List of triggered alert results.
        """
        triggered_alerts = []
        for rule in self.rules:
            data = all_stock_data.get(rule.symbol)
            if data is None:
                continue
            result = self.evaluate_rule(rule, data)
            if result["triggered"]:
                triggered_alerts.append(result)
        return triggered_alerts
