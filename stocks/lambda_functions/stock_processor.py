"""
Lambda Function: stock_processor

Triggered by SQS events when stock data is updated.
Processes stock data, runs analytics using stock_event_engine,
and stores results in DynamoDB.

This function is designed to be deployed as an AWS Lambda function
with SQS as its event trigger.
"""

import json
import logging
import os
import time
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any

import boto3
from botocore.exceptions import ClientError

# Import stock_event_engine for analytics
# In Lambda deployment, this must be included in the deployment package
try:
    from stock_event_engine.indicators import StockAnalyzer
    from stock_event_engine.signals import SignalDetector
    from stock_event_engine.exceptions import InsufficientDataError, StockDataError
except ImportError:
    # Fallback for local development
    StockAnalyzer = None
    SignalDetector = None

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb', region_name=os.getenv('AWS_DEFAULT_REGION', 'us-east-1'))
cloudwatch_logs = boto3.client('logs', region_name=os.getenv('AWS_DEFAULT_REGION', 'us-east-1'))
sns_client = boto3.client('sns', region_name=os.getenv('AWS_DEFAULT_REGION', 'us-east-1'))


def lambda_handler(event, context):
    """
    Lambda entry point — triggered by SQS.

    Event structure (SQS trigger):
    {
        "Records": [
            {
                "body": "{\"event_type\": \"STOCK_UPDATE\", \"symbol\": \"AAPL\", \"price\": 180}",
                ...
            }
        ]
    }

    Args:
        event: SQS event with Records array.
        context: Lambda context object.

    Returns:
        Processing result dictionary.
    """
    start_time = time.time()
    processed = 0
    errors = 0
    results = []

    logger.info(f"stock_processor invoked with {len(event.get('Records', []))} records.")

    for record in event.get('Records', []):
        try:
            # Parse the SQS message body
            body = json.loads(record.get('body', '{}'))
            event_type = body.get('event_type', 'UNKNOWN')
            symbol = body.get('symbol', '')

            logger.info(f"Processing event: {event_type} for {symbol}")

            if event_type == 'STOCK_UPDATE':
                result = process_stock_update(body)
                results.append(result)
            elif event_type == 'ANALYTICS_REQUEST':
                result = process_analytics_request(body)
                results.append(result)
            else:
                logger.warning(f"Unknown event type: {event_type}")

            processed += 1

        except Exception as e:
            errors += 1
            logger.error(f"Error processing record: {e}")

    # Calculate execution duration
    duration_ms = int((time.time() - start_time) * 1000)

    # Log execution to CloudWatch
    log_lambda_execution('stock_processor', processed, errors, duration_ms)

    return {
        'statusCode': 200,
        'body': json.dumps({
            'processed': processed,
            'errors': errors,
            'duration_ms': duration_ms,
            'results': results,
        })
    }


def process_stock_update(event_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process a stock update event.

    1. Extract stock data from the event
    2. Run technical analysis using stock_event_engine
    3. Store analytics results in DynamoDB
    """
    symbol = event_data.get('symbol', '')
    price = float(event_data.get('price', 0))
    volume = int(event_data.get('volume', 0))
    change_pct = float(event_data.get('change_percent', 0))
    timestamp = event_data.get('timestamp', datetime.utcnow().isoformat())

    result = {
        'symbol': symbol,
        'status': 'processed',
        'analytics': {},
    }

    # Store the raw stock data in DynamoDB
    store_stock_data(symbol, price, volume, change_pct, timestamp)

    # Run analytics if stock_event_engine is available
    if StockAnalyzer:
        analytics = run_analytics(symbol, price)
        result['analytics'] = analytics

        # Store analytics results
        store_analytics(symbol, analytics)

    logger.info(f"Stock update processed: {symbol} @ ${price:.2f}")
    return result


def process_analytics_request(event_data: Dict[str, Any]) -> Dict[str, Any]:
    """Process an analytics request event."""
    symbol = event_data.get('symbol', '')
    analysis_type = event_data.get('analysis_type', 'FULL')

    result = {
        'symbol': symbol,
        'analysis_type': analysis_type,
        'status': 'completed',
    }

    # Fetch historical data from DynamoDB for analysis
    historical = fetch_historical_prices(symbol)

    if historical and StockAnalyzer:
        analytics = run_full_analysis(symbol, historical)
        result['analytics'] = analytics
        store_analytics(symbol, analytics)

    return result


def run_analytics(symbol: str, current_price: float) -> Dict[str, Any]:
    """Run quick analytics on current price data."""
    result = {}

    try:
        # Create a simple price array for demonstration
        # In production, you'd fetch historical prices from DynamoDB
        prices = fetch_historical_prices(symbol)
        if not prices:
            prices = [current_price]

        if len(prices) >= 2:
            analyzer = StockAnalyzer(prices, symbol)
            result['summary'] = analyzer.summary()

            if len(prices) >= 14:
                rsi = analyzer.rsi()
                result['rsi'] = round(float(rsi[-1]), 4)

            if len(prices) >= 20:
                sma = analyzer.moving_average(20)
                result['sma_20'] = round(float(sma[-1]), 4)

                upper, middle, lower = analyzer.bollinger_bands()
                result['bollinger'] = {
                    'upper': round(float(upper[-1]), 4),
                    'middle': round(float(middle[-1]), 4),
                    'lower': round(float(lower[-1]), 4),
                }

    except (InsufficientDataError, StockDataError) as e:
        result['error'] = str(e)

    return result


def run_full_analysis(symbol: str, prices: list) -> Dict[str, Any]:
    """Run comprehensive analysis using stock_event_engine."""
    result = {}

    try:
        analyzer = StockAnalyzer(prices, symbol)
        detector = SignalDetector(prices, symbol)

        result['summary'] = analyzer.summary()
        result['signals'] = detector.generate_signals()

        if len(prices) >= 20:
            result['sma_20'] = round(float(analyzer.moving_average(20)[-1]), 4)
        if len(prices) >= 50:
            result['sma_50'] = round(float(analyzer.moving_average(50)[-1]), 4)
        if len(prices) >= 2:
            result['volatility'] = round(analyzer.volatility(), 4)

    except (InsufficientDataError, StockDataError) as e:
        result['error'] = str(e)

    return result


def store_stock_data(symbol: str, price: float, volume: int,
                     change_pct: float, timestamp: str):
    """Store stock data in DynamoDB."""
    try:
        table_name = os.getenv('DYNAMODB_STOCKS_TABLE', 'stock_data')
        table = dynamodb.Table(table_name)
        table.put_item(Item={
            'symbol': symbol,
            'timestamp': timestamp,
            'price': Decimal(str(price)),
            'volume': volume,
            'change_percent': Decimal(str(change_pct)),
        })
    except ClientError as e:
        logger.error(f"Error storing stock data: {e}")


def store_analytics(symbol: str, analytics: Dict):
    """Store analytics results in DynamoDB."""
    try:
        table_name = os.getenv('DYNAMODB_ANALYTICS_TABLE', 'analytics_results')
        table = dynamodb.Table(table_name)
        item = {
            'symbol': symbol,
            'analysis_type': 'LAMBDA_ANALYTICS',
            'result_data': json.loads(json.dumps(analytics, default=str)),
            'computed_at': datetime.utcnow().isoformat(),
        }
        sanitized = json.loads(json.dumps(item), parse_float=Decimal)
        table.put_item(Item=sanitized)
    except ClientError as e:
        logger.error(f"Error storing analytics: {e}")


def fetch_historical_prices(symbol: str, limit: int = 100) -> list:
    """Fetch historical prices from DynamoDB."""
    try:
        table_name = os.getenv('DYNAMODB_STOCKS_TABLE', 'stock_data')
        table = dynamodb.Table(table_name)
        response = table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key('symbol').eq(symbol),
            ScanIndexForward=True,
            Limit=limit,
        )
        items = response.get('Items', [])
        return [float(item['price']) for item in items if 'price' in item]
    except ClientError as e:
        logger.error(f"Error fetching historical prices: {e}")
        return []


def log_lambda_execution(function_name: str, processed: int,
                         errors: int, duration_ms: int):
    """Log Lambda execution metrics to CloudWatch."""
    try:
        log_group = os.getenv('CLOUDWATCH_LOG_GROUP', 'stock-platform-logs')
        log_stream = 'lambda-executions'

        message = (
            f"[LAMBDA] Function: {function_name} | "
            f"Processed: {processed} | Errors: {errors} | "
            f"Duration: {duration_ms}ms"
        )
        logger.info(message)
    except Exception as e:
        logger.error(f"Error logging execution: {e}")
