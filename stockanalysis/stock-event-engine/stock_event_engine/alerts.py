"""
Alert engine module — stock_event_engine v0.1.0

Provides AlertRule, AlertEngine (OOP) and send_alert() (functional API)
for evaluating user-defined price/volume/change alert conditions.
"""

from typing import Dict, Any, List, Optional

from stock_event_engine.exceptions import AlertEvaluationError


class AlertRule:
    """
    Represents a single named alert rule.

    Args:
        symbol:    Stock ticker (e.g. "AAPL").
        condition: One of PRICE_ABOVE, PRICE_BELOW, VOLUME_ABOVE,
                   CHANGE_ABOVE, CHANGE_BELOW.
        threshold: The numeric trigger value.
        user_id:   Owner of the alert (optional).
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
                        f"Must be one of {self.VALID_CONDITIONS}",
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
    Evaluates a collection of AlertRules against current market data.

    Example:
        engine = AlertEngine()
        engine.add_rule(AlertRule("AAPL", "PRICE_ABOVE", 180.0, "user1"))
        triggered = engine.evaluate({"AAPL": {"price": 185.0, "volume": 1_000_000}})
    """

    def __init__(self):
        self.rules: List[AlertRule] = []

    def add_rule(self, rule: AlertRule):
        """Add an alert rule."""
        self.rules.append(rule)

    def remove_rules_for_user(self, user_id: str):
        """Remove all rules belonging to a user."""
        self.rules = [r for r in self.rules if r.user_id != user_id]

    def evaluate_rule(self, rule: AlertRule, stock_data: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate a single rule against market data."""
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
        except (ValueError, TypeError) as exc:
            raise AlertEvaluationError(
                rule=str(rule.to_dict()),
                message=f"Error evaluating rule: {exc}",
            )

    def evaluate(self, all_stock_data: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Evaluate all rules and return only those that triggered.

        Args:
            all_stock_data: {symbol: {"price": float, "volume": int, "change_percent": float}}
        """
        triggered = []
        for rule in self.rules:
            data = all_stock_data.get(rule.symbol)
            if data is None:
                continue
            result = self.evaluate_rule(rule, data)
            if result["triggered"]:
                triggered.append(result)
        return triggered


# ── Functional API ────────────────────────────────────────────────────────


def send_alert(
    symbol: str,
    condition: str,
    threshold: float,
    stock_data: Dict[str, Any],
    user_id: str = "",
) -> Dict[str, Any]:
    """
    Evaluate a single alert condition and return the result.

    This is the recommended functional entry point. Internally creates an
    AlertRule + AlertEngine and runs a single evaluation.

    Args:
        symbol:     Stock ticker (e.g. "AAPL").
        condition:  PRICE_ABOVE | PRICE_BELOW | VOLUME_ABOVE |
                    CHANGE_ABOVE | CHANGE_BELOW
        threshold:  The numeric trigger value.
        stock_data: Dict with keys ``price``, ``volume``, ``change_percent``.
        user_id:    Optional owner identifier.

    Returns:
        Dict with keys:
            - triggered (bool)     Whether the alert fired
            - message (str)        Human-readable description
            - rule (dict)          The rule that was evaluated
            - current_price (float)
            - current_volume (float)

    Example:
        result = send_alert("AAPL", "PRICE_ABOVE", 180.0,
                            {"price": 185.0, "volume": 1_000_000})
        if result["triggered"]:
            print(result["message"])
    """
    rule = AlertRule(symbol, condition, threshold, user_id)
    engine = AlertEngine()
    engine.add_rule(rule)
    return engine.evaluate_rule(rule, stock_data)
