"""
Portfolio analytics module for the Django stocks app.

Wraps stock_event_engine.portfolio for Django-level portfolio analysis
with AWS integration.
"""

import logging
from typing import Dict, Any, List
from datetime import datetime

from stock_event_engine.portfolio import PortfolioAnalyzer
from stock_event_engine.exceptions import PortfolioError, InsufficientDataError
from stocks.services.dynamodb_service import DynamoDBService
from stocks.services.s3_service import S3Service

logger = logging.getLogger('stocks')


class PortfolioService:
    """
    Provides portfolio analysis for Django views.
    Integrates with stock_event_engine and AWS storage.
    """

    def __init__(self):
        self.dynamodb = DynamoDBService()
        self.s3 = S3Service()

    def analyze_portfolio(self, holdings: Dict[str, Dict]) -> Dict[str, Any]:
        """
        Perform full portfolio analysis.

        Args:
            holdings: Dict mapping symbol → {"prices": [...], "weight": float}

        Returns:
            Complete portfolio analysis result.
        """
        try:
            analyzer = PortfolioAnalyzer()

            for symbol, data in holdings.items():
                analyzer.add_holding(
                    symbol=symbol,
                    prices=data.get('prices', []),
                    weight=data.get('weight', 1.0),
                )

            returns = analyzer.portfolio_return()
            risk = analyzer.risk_analysis()

            result = {
                'returns': returns,
                'risk': risk,
                'computed_at': datetime.utcnow().isoformat(),
                'holdings': list(holdings.keys()),
            }

            # Upload report to S3
            self.s3.upload_json_report(result, 'PORTFOLIO', 'portfolio_analysis')

            # Store summary in DynamoDB
            self.dynamodb.store_analytics_result(
                'PORTFOLIO', 'PORTFOLIO', result
            )

            logger.info(f"Portfolio analysis completed for {len(holdings)} holdings.")
            return result

        except (PortfolioError, InsufficientDataError) as e:
            logger.error(f"Portfolio analysis error: {e}")
            return {'error': str(e)}

    def calculate_returns(self, holdings: Dict[str, Dict]) -> Dict[str, Any]:
        """Calculate portfolio returns only."""
        try:
            analyzer = PortfolioAnalyzer()
            for symbol, data in holdings.items():
                analyzer.add_holding(symbol, data['prices'], data.get('weight', 1.0))
            return analyzer.portfolio_return()
        except (PortfolioError, InsufficientDataError) as e:
            return {'error': str(e)}

    def calculate_risk(self, holdings: Dict[str, Dict]) -> Dict[str, Any]:
        """Calculate portfolio risk metrics only."""
        try:
            analyzer = PortfolioAnalyzer()
            for symbol, data in holdings.items():
                analyzer.add_holding(symbol, data['prices'], data.get('weight', 1.0))
            return analyzer.risk_analysis()
        except (PortfolioError, InsufficientDataError) as e:
            return {'error': str(e)}
