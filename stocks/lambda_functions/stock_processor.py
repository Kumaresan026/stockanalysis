"""
Lambda Function: stock_processor

Triggered automatically by SQS when stock data is updated.

Pipeline:
1. Receives SQS event (STOCK_UPDATE or ANALYTICS_REQUEST)
2. Stores raw stock data in DynamoDB (stock_data table)
3. Runs technical analytics using stock_event_engine
4. Uploads analytics report to S3
5. Invokes alert_handler Lambda to evaluate active alert rules

This function is deployed to AWS Lambda and uses its attached IAM role (LabRole)
for all AWS SDK calls — no explicit credentials are needed here.
"""

import boto3
import json
import logging
import os
import time
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any

from botocore.exceptions import ClientError

# Import stock_event_engine (bundled in the zip package)
try:
    from stock_event_engine.indicators import StockAnalyzer
    from stock_event_engine.signals import SignalDetector
    from stock_event_engine.exceptions import InsufficientDataError, StockDataError
except ImportError:
    StockAnalyzer = None
    SignalDetector = None

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── AWS Clients ────────────────────────────────────────────────────────────────
# When running inside Lambda, these auto-use the attached IAM role (LabRole)
_region = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')
dynamodb = boto3.resource('dynamodb', region_name=_region)
s3_client = boto3.client('s3', region_name=_region)
lambda_client = boto3.client('lambda', region_name=_region)


# ── Lambda Entry Point ─────────────────────────────────────────────────────────

def lambda_handler(event, context):
    """
    Lambda entry point — triggered by SQS.

    SQS event structure:
    {
        "Records": [
            {
                "body": '{"event_type": "STOCK_UPDATE", "symbol": "AAPL", "price": 180, ...}',
                "messageId": "..."
            }
        ]
    }

    Args:
        event: SQS event dict with Records array.
        context: Lambda context object (unused).

    Returns:
        Processing summary dict.
    """
    start_time = time.time()
    processed = 0
    errors = 0
    results = []

    logger.info(f"stock_processor invoked with {len(event.get('Records', []))} records.")

    for record in event.get('Records', []):
        try:
            body_raw = record.get('body', '{}')
            body = json.loads(body_raw) if isinstance(body_raw, str) else body_raw
            event_type = body.get('event_type', 'UNKNOWN')
            symbol = body.get('symbol', '')

            logger.info(f"Processing {event_type} for {symbol}")

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
            logger.error(f"Error processing SQS record: {e}", exc_info=True)

    duration_ms = int((time.time() - start_time) * 1000)
    logger.info(
        f"stock_processor done: processed={processed}, errors={errors}, "
        f"duration={duration_ms}ms"
    )

    return {
        'statusCode': 200,
        'body': json.dumps({
            'processed': processed,
            'errors': errors,
            'duration_ms': duration_ms,
            'results': results,
        })
    }


# ── Event Processors ───────────────────────────────────────────────────────────

def process_stock_update(event_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Full pipeline for a STOCK_UPDATE event:
      1. Store raw data in DynamoDB
      2. Fetch price history from DynamoDB for analytics
      3. Run technical analytics
      4. Upload analytics report to S3
      5. Invoke alert_handler Lambda asynchronously
    """
    symbol = event_data.get('symbol', '')
    price = float(event_data.get('price', 0))
    volume = int(event_data.get('volume', 0))
    change_pct = float(event_data.get('change_percent', 0))
    timestamp = event_data.get('timestamp', datetime.utcnow().isoformat())

    result = {'symbol': symbol, 'status': 'processed', 'analytics': {}}

    # Step 1: Store raw stock data in DynamoDB
    store_stock_data(symbol, price, volume, change_pct, timestamp)

    # Step 2: Fetch historical prices for analytics
    prices = fetch_historical_prices(symbol)
    if not prices:
        prices = [price]

    # Step 3: Run analytics
    analytics = {}
    if StockAnalyzer and len(prices) >= 2:
        analytics = run_analytics(symbol, prices)
        result['analytics'] = analytics

        # Store analytics results in DynamoDB
        store_analytics(symbol, analytics)

    # Step 4: Upload analytics report to S3
    if analytics:
        upload_analytics_to_s3(symbol, analytics)

    # Step 5: Invoke alert_handler Lambda (fire-and-forget)
    invoke_alert_handler(symbol, price, volume, change_pct)

    logger.info(f"Stock update pipeline complete: {symbol} @ ${price:.2f}")
    return result


def process_analytics_request(event_data: Dict[str, Any]) -> Dict[str, Any]:
    """Process an ANALYTICS_REQUEST event."""
    symbol = event_data.get('symbol', '')
    analysis_type = event_data.get('analysis_type', 'FULL')

    result = {'symbol': symbol, 'analysis_type': analysis_type, 'status': 'completed'}

    prices = fetch_historical_prices(symbol)
    if prices and StockAnalyzer:
        analytics = run_full_analysis(symbol, prices)
        result['analytics'] = analytics
        store_analytics(symbol, analytics)
        if analytics:
            upload_analytics_to_s3(symbol, analytics)

    return result


# ── Analytics ──────────────────────────────────────────────────────────────────

def run_analytics(symbol: str, prices: list) -> Dict[str, Any]:
    """Run technical analytics on the price history."""
    result = {}
    try:
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
    except Exception as e:
        logger.error(f"Analytics error for {symbol}: {e}")
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
    except Exception as e:
        logger.error(f"Full analysis error for {symbol}: {e}")
        result['error'] = str(e)
    return result


# ── DynamoDB ───────────────────────────────────────────────────────────────────

def store_stock_data(symbol: str, price: float, volume: int,
                     change_pct: float, timestamp: str):
    """Store raw stock data in the stock_data DynamoDB table."""
    try:
        table = dynamodb.Table(os.getenv('DYNAMODB_STOCKS_TABLE', 'stock_data'))
        table.put_item(Item={
            'symbol': symbol,
            'timestamp': timestamp,
            'price': Decimal(str(price)),
            'volume': volume,
            'change_percent': Decimal(str(change_pct)),
        })
        logger.info(f"DynamoDB: stored stock data for {symbol}")
    except ClientError as e:
        logger.error(f"DynamoDB store_stock_data error: {e}")


def store_analytics(symbol: str, analytics: Dict):
    """Store analytics results in the analytics_results DynamoDB table."""
    try:
        table = dynamodb.Table(os.getenv('DYNAMODB_ANALYTICS_TABLE', 'analytics_results'))
        item = {
            'symbol': symbol,
            'analysis_type': 'LAMBDA_ANALYTICS',
            'result_data': json.loads(json.dumps(analytics, default=str)),
            'computed_at': datetime.utcnow().isoformat(),
        }
        # Convert floats to Decimal for DynamoDB
        sanitized = json.loads(json.dumps(item), parse_float=Decimal)
        table.put_item(Item=sanitized)
        logger.info(f"DynamoDB: stored analytics for {symbol}")
    except ClientError as e:
        logger.error(f"DynamoDB store_analytics error: {e}")


def fetch_historical_prices(symbol: str, limit: int = 100) -> list:
    """Fetch historical price list for a symbol from DynamoDB."""
    try:
        table = dynamodb.Table(os.getenv('DYNAMODB_STOCKS_TABLE', 'stock_data'))
        response = table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key('symbol').eq(symbol),
            ScanIndexForward=True,
            Limit=limit,
        )
        return [float(item['price']) for item in response.get('Items', []) if 'price' in item]
    except ClientError as e:
        logger.error(f"DynamoDB fetch_historical_prices error: {e}")
        return []


# ── S3 ─────────────────────────────────────────────────────────────────────────

def upload_analytics_to_s3(symbol: str, analytics: Dict):
    """
    Upload the analytics report as a JSON file to S3.
    Key: analytics/{symbol}/{timestamp}.json
    """
    bucket = os.getenv('S3_BUCKET_NAME', 'stock-platform-reports-2024')
    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    key = f"analytics/{symbol}/{timestamp}.json"

    try:
        report = {
            'symbol': symbol,
            'computed_at': datetime.utcnow().isoformat(),
            'source': 'lambda:stock_processor',
            'analytics': analytics,
        }
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(report, indent=2, default=str),
            ContentType='application/json',
        )
        logger.info(f"S3: uploaded analytics report → s3://{bucket}/{key}")
    except ClientError as e:
        logger.error(f"S3 upload error for {symbol}: {e}")


# ── Lambda Chaining ────────────────────────────────────────────────────────────

def invoke_alert_handler(symbol: str, price: float, volume: int, change_pct: float):
    """
    Asynchronously invoke alert_handler Lambda to evaluate active alerts.
    This chains the two Lambda functions without going through SQS again.
    """
    function_name = os.getenv('LAMBDA_ALERT_HANDLER', 'alert_handler')
    payload = {
        'symbol': symbol,
        'price': price,
        'volume': volume,
        'change_percent': change_pct,
    }
    try:
        lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='Event',  # async, fire-and-forget
            Payload=json.dumps(payload).encode('utf-8'),
        )
        logger.info(f"Lambda chain: invoked alert_handler for {symbol}")
    except ClientError as e:
        logger.error(f"Error chaining alert_handler: {e}")
