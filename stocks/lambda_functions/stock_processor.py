"""
Lambda Function: stock_processor

Triggered automatically by SQS when stock data is updated.

Pipeline:
1. Receives SQS event (STOCK_UPDATE or ANALYTICS_REQUEST)
2. Stores raw stock data in DynamoDB (stock_data table)
3. Runs technical analytics using stock_event_engine
4. Uploads analytics report to S3
5. Invokes alert_handler Lambda to evaluate active alert rules

Deployed to AWS Lambda with LabRole IAM role.
boto3 clients are initialized LAZILY inside functions (not at module level)
to avoid credential errors when imported locally by Django.
"""

import json
import logging
import os
import time
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any

from botocore.exceptions import ClientError

try:
    from stock_event_engine.indicators import StockAnalyzer
    from stock_event_engine.signals import SignalDetector
    from stock_event_engine.exceptions import InsufficientDataError, StockDataError
except ImportError:
    StockAnalyzer  = None
    SignalDetector = None

# Configure structured CloudWatch logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

_LOG_FORMAT = "[{service}] [{action}] [{status}] {detail}"


def _log(service, action, status, detail=""):
    """Emit a structured log line readable in CloudWatch."""
    logger.info(_LOG_FORMAT.format(
        service=service, action=action, status=status, detail=detail
    ))


# ── Lazy AWS client cache ─────────────────────────────────────────────────────
# Clients created on first call, NOT at import time.
# This allows the file to be imported by Django without credentials.

_region = None
_dynamodb_resource = None
_s3_client = None
_lambda_client = None


def _get_region():
    global _region
    if _region is None:
        _region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    return _region


def _get_dynamodb():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        import boto3
        _dynamodb_resource = boto3.resource("dynamodb", region_name=_get_region())
        _log("DynamoDB", "CLIENT_INIT", "OK", f"region={_get_region()}")
    return _dynamodb_resource


def _get_s3():
    global _s3_client
    if _s3_client is None:
        import boto3
        _s3_client = boto3.client("s3", region_name=_get_region())
        _log("S3", "CLIENT_INIT", "OK", f"region={_get_region()}")
    return _s3_client


def _get_lambda():
    global _lambda_client
    if _lambda_client is None:
        import boto3
        _lambda_client = boto3.client("lambda", region_name=_get_region())
        _log("Lambda", "CLIENT_INIT", "OK", f"region={_get_region()}")
    return _lambda_client


# ── Lambda Entry Point ────────────────────────────────────────────────────────

def lambda_handler(event, context):
    """
    Lambda entry point — triggered by SQS.

    SQS event format:
    {
      "Records": [
        { "body": '{"event_type":"STOCK_UPDATE","symbol":"AAPL","price":180,...}' }
      ]
    }
    """
    start_time = time.time()
    processed  = 0
    errors     = 0
    results    = []

    record_count = len(event.get("Records", []))
    _log("Lambda", "INVOKED", "START", f"records={record_count}")
    logger.info(f"stock_processor Lambda received {record_count} SQS record(s).")

    for record in event.get("Records", []):
        try:
            body_raw   = record.get("body", "{}")
            body       = json.loads(body_raw) if isinstance(body_raw, str) else body_raw
            event_type = body.get("event_type", "UNKNOWN")
            symbol     = body.get("symbol", "")

            _log("SQS", "MESSAGE_RECEIVED", "OK", f"event_type={event_type} symbol={symbol}")
            logger.info(f"Processing {event_type} for {symbol}")

            if event_type == "STOCK_UPDATE":
                result = process_stock_update(body)
                results.append(result)
            elif event_type == "ANALYTICS_REQUEST":
                result = process_analytics_request(body)
                results.append(result)
            elif event_type == "ALERT_CREATED":
                _log("SQS", "ALERT_CREATED_RECEIVED", "OK", f"symbol={symbol}")
                results.append({"symbol": symbol, "status": "alert_noted"})
            elif event_type == "DIAGNOSTIC":
                _log("SQS", "DIAGNOSTIC_RECEIVED", "OK", "diagnostic test message")
                results.append({"event_type": "DIAGNOSTIC", "status": "ok"})
            else:
                logger.warning(f"Unknown event type: {event_type}")
                _log("SQS", "UNKNOWN_EVENT", "WARN", f"event_type={event_type}")

            processed += 1

        except Exception as e:
            errors += 1
            logger.error(f"Error processing SQS record: {e}", exc_info=True)
            _log("Lambda", "RECORD_PROCESSING", "ERROR", str(e))

    duration_ms = int((time.time() - start_time) * 1000)
    _log("Lambda", "INVOKED", "DONE",
         f"processed={processed} errors={errors} duration_ms={duration_ms}")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "processed":   processed,
            "errors":      errors,
            "duration_ms": duration_ms,
            "results":     results,
        }),
    }


# ── Event Processors ──────────────────────────────────────────────────────────

def process_stock_update(event_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Full pipeline for STOCK_UPDATE:
      1. Store raw data in DynamoDB
      2. Fetch price history for analytics
      3. Run technical analytics
      4. Upload report to S3
      5. Chain alert_handler Lambda
    """
    symbol      = event_data.get("symbol", "")
    price       = float(event_data.get("price", 0))
    volume      = int(event_data.get("volume", 0))
    change_pct  = float(event_data.get("change_percent", 0))
    timestamp   = event_data.get("timestamp", datetime.utcnow().isoformat())

    result = {"symbol": symbol, "status": "processed", "analytics": {}}

    # Step 1: DynamoDB write
    store_stock_data(symbol, price, volume, change_pct, timestamp)

    # Step 2: Fetch history for analytics
    prices = fetch_historical_prices(symbol) or [price]

    # Step 3: Run analytics
    analytics = {}
    if StockAnalyzer and len(prices) >= 2:
        analytics         = run_analytics(symbol, prices)
        result["analytics"] = analytics
        if analytics:
            store_analytics(symbol, analytics)

    # Step 4: Upload to S3
    if analytics:
        upload_analytics_to_s3(symbol, analytics)

    # Step 5: Chain alert_handler
    invoke_alert_handler(symbol, price, volume, change_pct)

    logger.info(f"Stock update pipeline complete: {symbol} @ ${price:.2f}")
    return result


def process_analytics_request(event_data: Dict[str, Any]) -> Dict[str, Any]:
    symbol        = event_data.get("symbol", "")
    analysis_type = event_data.get("analysis_type", "FULL")
    result        = {"symbol": symbol, "analysis_type": analysis_type, "status": "completed"}

    prices = fetch_historical_prices(symbol)
    if prices and StockAnalyzer:
        analytics         = run_full_analysis(symbol, prices)
        result["analytics"] = analytics
        store_analytics(symbol, analytics)
        if analytics:
            upload_analytics_to_s3(symbol, analytics)

    return result


# ── Analytics ─────────────────────────────────────────────────────────────────

def run_analytics(symbol: str, prices: list) -> Dict[str, Any]:
    result = {}
    try:
        analyzer = StockAnalyzer(prices, symbol)
        result["summary"] = analyzer.summary()
        if len(prices) >= 14:
            rsi = analyzer.rsi()
            result["rsi"] = round(float(rsi[-1]), 4)
        if len(prices) >= 20:
            sma = analyzer.moving_average(20)
            result["sma_20"] = round(float(sma[-1]), 4)
            upper, middle, lower = analyzer.bollinger_bands()
            result["bollinger"] = {
                "upper":  round(float(upper[-1]),  4),
                "middle": round(float(middle[-1]), 4),
                "lower":  round(float(lower[-1]),  4),
            }
        _log("Analytics", "RUN_ANALYTICS", "OK", f"symbol={symbol} keys={list(result.keys())}")
    except (InsufficientDataError, StockDataError) as e:
        result["error"] = str(e)
        _log("Analytics", "RUN_ANALYTICS", "WARN", str(e))
    except Exception as e:
        logger.error(f"Analytics error for {symbol}: {e}")
        result["error"] = str(e)
        _log("Analytics", "RUN_ANALYTICS", "ERROR", str(e))
    return result


def run_full_analysis(symbol: str, prices: list) -> Dict[str, Any]:
    result = {}
    try:
        analyzer  = StockAnalyzer(prices, symbol)
        detector  = SignalDetector(prices, symbol)
        result["summary"] = analyzer.summary()
        result["signals"] = detector.generate_signals()
        if len(prices) >= 20:
            result["sma_20"] = round(float(analyzer.moving_average(20)[-1]), 4)
        if len(prices) >= 50:
            result["sma_50"] = round(float(analyzer.moving_average(50)[-1]), 4)
        if len(prices) >= 2:
            result["volatility"] = round(analyzer.volatility(), 4)
    except Exception as e:
        logger.error(f"Full analysis error for {symbol}: {e}")
        result["error"] = str(e)
    return result


# ── DynamoDB ──────────────────────────────────────────────────────────────────

def store_stock_data(symbol: str, price: float, volume: int,
                     change_pct: float, timestamp: str):
    """Write raw stock tick to DynamoDB stock_data table."""
    table_name = os.getenv("DYNAMODB_STOCKS_TABLE", "stock_data")
    try:
        table = _get_dynamodb().Table(table_name)
        table.put_item(Item={
            "symbol":         symbol,
            "timestamp":      timestamp,
            "price":          Decimal(str(price)),
            "volume":         volume,
            "change_percent": Decimal(str(change_pct)),
        })
        _log("DynamoDB", "RECORD_INSERTED", "OK", f"table={table_name} symbol={symbol} price={price}")
    except ClientError as e:
        _log("DynamoDB", "RECORD_INSERTED", "ERROR", str(e))
        logger.error(f"DynamoDB store_stock_data error: {e}")


def store_analytics(symbol: str, analytics: Dict):
    """Write analytics result to DynamoDB analytics_results table."""
    table_name = os.getenv("DYNAMODB_ANALYTICS_TABLE", "analytics_results")
    try:
        table = _get_dynamodb().Table(table_name)
        item  = {
            "symbol":        symbol,
            "analysis_type": "LAMBDA_ANALYTICS",
            "result_data":   json.loads(json.dumps(analytics, default=str)),
            "computed_at":   datetime.utcnow().isoformat(),
        }
        sanitized = json.loads(json.dumps(item), parse_float=Decimal)
        table.put_item(Item=sanitized)
        _log("DynamoDB", "ANALYTICS_STORED", "OK", f"table={table_name} symbol={symbol}")
    except ClientError as e:
        _log("DynamoDB", "ANALYTICS_STORED", "ERROR", str(e))
        logger.error(f"DynamoDB store_analytics error: {e}")


def fetch_historical_prices(symbol: str, limit: int = 100) -> list:
    """Query historical prices for a symbol from DynamoDB."""
    table_name = os.getenv("DYNAMODB_STOCKS_TABLE", "stock_data")
    try:
        import boto3.dynamodb.conditions as cond
        table    = _get_dynamodb().Table(table_name)
        response = table.query(
            KeyConditionExpression=cond.Key("symbol").eq(symbol),
            ScanIndexForward=True,
            Limit=limit,
        )
        prices = [float(item["price"]) for item in response.get("Items", []) if "price" in item]
        _log("DynamoDB", "HISTORY_FETCHED", "OK", f"symbol={symbol} count={len(prices)}")
        return prices
    except ClientError as e:
        _log("DynamoDB", "HISTORY_FETCHED", "ERROR", str(e))
        logger.error(f"DynamoDB fetch_historical_prices error: {e}")
        return []


# ── S3 ────────────────────────────────────────────────────────────────────────

def upload_analytics_to_s3(symbol: str, analytics: Dict):
    """Upload analytics JSON report to S3."""
    bucket    = os.getenv("S3_BUCKET_NAME", "stock-platform-reports-2024")
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    key       = f"analytics/{symbol}/{timestamp}.json"

    try:
        report = {
            "symbol":      symbol,
            "computed_at": datetime.utcnow().isoformat(),
            "source":      "lambda:stock_processor",
            "analytics":   analytics,
        }
        _get_s3().put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(report, indent=2, default=str),
            ContentType="application/json",
        )
        _log("S3", "UPLOAD", "OK", f"s3://{bucket}/{key}")
        logger.info(f"S3: uploaded analytics report -> s3://{bucket}/{key}")
    except ClientError as e:
        _log("S3", "UPLOAD", "ERROR", str(e))
        logger.error(f"S3 upload error for {symbol}: {e}")


# ── Lambda Chaining ───────────────────────────────────────────────────────────

def invoke_alert_handler(symbol: str, price: float, volume: int, change_pct: float):
    """Asynchronously invoke alert_handler Lambda (fire-and-forget)."""
    function_name = os.getenv("LAMBDA_ALERT_HANDLER", "alert_handler")
    payload = {
        "symbol":         symbol,
        "price":          price,
        "volume":         volume,
        "change_percent": change_pct,
    }
    try:
        _get_lambda().invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json.dumps(payload).encode("utf-8"),
        )
        _log("Lambda", "CHAIN_INVOKE", "OK",
             f"function={function_name} symbol={symbol}")
        logger.info(f"Lambda chain: invoked alert_handler for {symbol}")
    except ClientError as e:
        _log("Lambda", "CHAIN_INVOKE", "ERROR", str(e))
        logger.error(f"Error chaining alert_handler: {e}")
