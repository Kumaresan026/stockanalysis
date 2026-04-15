"""
Django views for the stocks app.

Implements all user-facing views: dashboard, stock detail, search,
watchlist, alerts, analytics, and authentication flows.
All views integrate with AWS services via the service layer.
"""

import json
import logging
import os
import threading
import uuid
from datetime import datetime
from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone

from stocks.models import UserProfile, Stock, Watchlist, Alert, AnalyticsResult
from stocks.forms import (
    UserRegistrationForm,
    UserProfileForm,
    AlertForm,
    StockSearchForm,
    WatchlistForm,
)
from stocks.api.stock_api import StockAPIService
from stocks.services.dynamodb_service import DynamoDBService
from stocks.services.s3_service import S3Service
from stocks.services.sqs_service import SQSService
from stocks.services.sns_service import SNSService
from stocks.services.cloudwatch_service import CloudWatchService
from stocks.services.alert_evaluator import evaluate_and_notify
from stocks.events.producer import StockEventProducer
from stocks.analytics.indicators import StockIndicatorService
from stocks.services.lambda_service import LambdaService

logger = logging.getLogger('stocks')

# Non-AWS services — safe to initialize once at startup (no AWS credentials needed)
api_service = StockAPIService()
indicator_service = StockIndicatorService()

# ── Per-request AWS service factories ────────────────────────────────────────
# ALWAYS call these INSIDE view/function bodies — NEVER at module level.
# Module-level instantiation triggers STS network calls at gunicorn startup,
# which blocks worker boot and causes HTTP 500 errors when credentials expire.
def _sns():            return SNSService()
def _dynamodb():       return DynamoDBService()
def _s3():             return S3Service()
def _sqs():            return SQSService()
def _cloudwatch():     return CloudWatchService()
def _event_producer(): return StockEventProducer()
def _lambda():         return LambdaService()


# ── Safe AWS execution helpers ────────────────────────────────────────────────

def safe_aws_call(func, default=None):
    """
    Execute an AWS call, silently returning ``default`` on any exception.

    Ensures that a failed or timed-out AWS call NEVER propagates to a Django
    view and causes an HTTP 500 error.
    """
    try:
        return func()
    except Exception as exc:
        logger.warning("[AWS] safe_aws_call suppressed %s: %s", type(exc).__name__, exc)
        return default


def fire_and_forget(func):
    """
    Run an AWS operation on a background daemon thread.

    The HTTP response is returned to the user immediately.  The cloud write
    (DynamoDB / SQS / SNS / CloudWatch) completes — or silently fails — in
    the background without affecting response latency or raising exceptions.
    """
    def _worker():
        try:
            func()
        except Exception as exc:
            logger.warning(
                "[AWS] background task suppressed %s: %s", type(exc).__name__, exc
            )
    threading.Thread(target=_worker, daemon=True).start()


# ═══════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════════

def dashboard(request):
    """Main dashboard — market overview with top gainers/losers."""
    # Fresh instances on every request — picks up rotated AWS Academy credentials
    dynamodb_service   = _dynamodb()
    sns_service        = _sns()
    cloudwatch_service = _cloudwatch()

    # Fetch top movers
    movers = api_service.get_top_movers(count=6)
    gainers = movers.get('gainers', [])
    losers = movers.get('losers', [])

    # Fallback: if no API key, fetch known symbols directly for display
    if not gainers and not losers:
        popular = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'TSLA', 'META', 'NFLX']
        all_quotes = [api_service.get_stock_quote(sym) for sym in popular]
        gainers = [q for q in all_quotes if q.get('change_percent', 0) >= 0][:4]
        losers  = [q for q in all_quotes if q.get('change_percent', 0) < 0][:4]
        # If all are positive (demo data uses static values), split the list
        if not losers:
            gainers, losers = all_quotes[:4], all_quotes[4:]

    # Log dashboard access to CloudWatch (best-effort — never crash the view)
    try:
        cloudwatch_service.log_system_event(
            'DASHBOARD_ACCESS',
            f"User: {request.user.username if request.user.is_authenticated else 'anonymous'}"
        )
    except Exception:
        pass

    # Get user's watchlist if authenticated
    watchlist_stocks = []
    if request.user.is_authenticated:
        try:
            watchlist_entries = Watchlist.objects.filter(user=request.user).select_related('stock')[:5]
            for entry in watchlist_entries:
                data = api_service.get_stock_quote(entry.stock.symbol)
                watchlist_stocks.append(data)
        except Exception:
            pass

    # Evaluate alerts for all stocks shown on the dashboard.
    # Wrapped in try/except — alert evaluation MUST NOT crash the page.
    try:
        all_dashboard_stocks = gainers + losers + watchlist_stocks
        seen_symbols = set()
        for stock_data in all_dashboard_stocks:
            sym = stock_data.get('symbol', '')
            if sym and sym not in seen_symbols:
                seen_symbols.add(sym)
                evaluate_and_notify(
                    symbol=sym,
                    price=stock_data.get('price', 0),
                    volume=stock_data.get('volume', 0),
                    change_percent=stock_data.get('change_percent', 0),
                    dynamodb_service=dynamodb_service,
                    sns_service=sns_service,
                )
    except Exception:
        pass

    context = {
        'gainers': gainers,
        'losers': losers,
        'watchlist_stocks': watchlist_stocks,
        'search_form': StockSearchForm(),
    }
    return render(request, 'stocks/dashboard.html', context)



# ═══════════════════════════════════════════════════════════════════════
# STOCK VIEWS
# ═══════════════════════════════════════════════════════════════════════

def stock_detail(request, symbol):
    """
    Stock detail page — price, volume, historical chart, analytics.

    Workflow:
    1. Fetch stock data from API
    2. Store in DynamoDB
    3. Send event to SQS
    4. Compute analytics
    """
    symbol = symbol.upper()

    # Step 1: Fetch real-time data — synchronous (required for page render)
    stock_data = api_service.get_stock_quote(symbol)
    history    = api_service.get_stock_history(symbol, days=90)

    # ── Background AWS writes ─────────────────────────────────────────────────
    # All cloud writes run on daemon threads so they NEVER delay the HTTP response.
    # Capture scalar values now; lambdas close over these locals (not the mutable
    # stock_data dict) so values are stable when the thread executes.
    _price   = stock_data.get('price', 0)
    _volume  = stock_data.get('volume', 0)
    _chg_pct = stock_data.get('change_percent', 0)
    _source  = stock_data.get('source', 'unknown')

    # Step 2: DynamoDB store — fire-and-forget
    fire_and_forget(lambda: _dynamodb().store_stock_data(
        symbol=symbol, price=_price, volume=_volume, change_percent=_chg_pct
    ))

    # Step 3: Alert evaluation — fire-and-forget (fresh service instances per thread)
    fire_and_forget(lambda: evaluate_and_notify(
        symbol=symbol, price=_price, volume=_volume, change_percent=_chg_pct,
        dynamodb_service=_dynamodb(), sns_service=_sns(),
    ))

    # Step 4: SQS → Lambda pipeline — fire-and-forget
    def _run_pipeline():
        sqs_ok = safe_aws_call(
            lambda: _event_producer().send_stock_update(
                symbol=symbol, price=_price, volume=_volume, change_percent=_chg_pct
            )
        )
        if not sqs_ok:
            # SQS unavailable — fall back to direct Lambda invoke
            safe_aws_call(lambda: _lambda().invoke_stock_processor(
                symbol=symbol, price=_price, volume=_volume, change_percent=_chg_pct
            ))

    fire_and_forget(_run_pipeline)

    # Step 5: CloudWatch log — fire-and-forget
    fire_and_forget(lambda: _cloudwatch().log_stock_fetch(symbol, _source, True))

    # Step 5: Compute analytics
    prices = [d['close'] for d in history] if history else []
    analytics = {}
    if prices:
        analytics = indicator_service.compute_indicators(symbol, prices)

    # Update or create Django model
    stock_obj, _ = Stock.objects.update_or_create(
        symbol=symbol,
        defaults={
            'name': stock_data.get('name', symbol),
            'current_price': Decimal(str(stock_data.get('price', 0))),
            'previous_close': Decimal(str(stock_data.get('previous_close', 0))),
            'volume': stock_data.get('volume', 0),
            'change_percent': Decimal(str(stock_data.get('change_percent', 0))),
            'last_updated': timezone.now(),
        }
    )

    # Check if user has it in watchlist
    in_watchlist = False
    if request.user.is_authenticated:
        in_watchlist = Watchlist.objects.filter(
            user=request.user, stock=stock_obj
        ).exists()

    # Prepare chart data
    chart_labels = [d['date'] for d in history] if history else []
    chart_prices = [d['close'] for d in history] if history else []
    chart_volumes = [d['volume'] for d in history] if history else []

    context = {
        'stock': stock_data,
        'stock_obj': stock_obj,
        'history': history,
        'analytics': analytics,
        'in_watchlist': in_watchlist,
        'chart_labels': json.dumps(chart_labels),
        'chart_prices': json.dumps(chart_prices),
        'chart_volumes': json.dumps(chart_volumes),
        'indicators': analytics.get('indicators', {}),
        'signals': analytics.get('signals', {}),
    }
    return render(request, 'stocks/stock_detail.html', context)


def stock_search(request):
    """Search for stocks by symbol or name."""
    query = request.GET.get('query', '').strip()
    results = []
    # Fresh instance on every request
    _cw = _cloudwatch()

    if query:
        results = api_service.search_stocks(query)
        try:
            _cw.log_system_event(
                'STOCK_SEARCH', f"Query: {query}, Results: {len(results)}"
            )
        except Exception:
            pass

    context = {
        'query': query,
        'results': results,
        'search_form': StockSearchForm(initial={'query': query}),
    }
    return render(request, 'stocks/stock_search.html', context)


# ═══════════════════════════════════════════════════════════════════════
# WATCHLIST
# ═══════════════════════════════════════════════════════════════════════

@login_required
def watchlist_view(request):
    """Display user's watchlist with live prices."""
    entries = Watchlist.objects.filter(user=request.user).select_related('stock')
    watchlist_data = []

    for entry in entries:
        quote = api_service.get_stock_quote(entry.stock.symbol)
        watchlist_data.append({
            'entry': entry,
            'quote': quote,
        })

    context = {
        'watchlist_data': watchlist_data,
        'form': WatchlistForm(),
    }
    return render(request, 'stocks/watchlist.html', context)


@login_required
def add_to_watchlist(request, symbol):
    """Add a stock to the user's watchlist."""
    symbol = symbol.upper()

    # Get or create the stock object
    stock_data = api_service.get_stock_quote(symbol)
    stock_obj, _ = Stock.objects.get_or_create(
        symbol=symbol,
        defaults={
            'name': stock_data.get('name', symbol),
            'current_price': Decimal(str(stock_data.get('price', 0))),
        }
    )

    # Add to watchlist
    _, created = Watchlist.objects.get_or_create(
        user=request.user,
        stock=stock_obj,
    )

    # Store in DynamoDB (best-effort — never crash the view)
    table_name = os.getenv('DYNAMODB_WATCHLIST_TABLE', 'user_watchlists')
    _db = _dynamodb()
    try:
        _db.put_item(
            table_name,
            {
                'user_id': str(request.user.id),
                'symbol': symbol,
                'added_at': datetime.utcnow().isoformat(),
            }
        )
    except Exception as e:
        logger.warning(f"[DynamoDB] watchlist put_item failed for {symbol}: {e}")

    if created:
        messages.success(request, f'{symbol} added to your watchlist.')
    else:
        messages.info(request, f'{symbol} is already in your watchlist.')

    return redirect('stock_detail', symbol=symbol)


@login_required
def remove_from_watchlist(request, symbol):
    """Remove a stock from the user's watchlist."""
    symbol = symbol.upper()
    Watchlist.objects.filter(
        user=request.user, stock__symbol=symbol
    ).delete()
    messages.success(request, f'{symbol} removed from your watchlist.')
    return redirect('watchlist')


# ═══════════════════════════════════════════════════════════════════════
# ALERTS
# ═══════════════════════════════════════════════════════════════════════

@login_required
def alerts_view(request):
    """
    Display user's alerts.

    Storage hierarchy:
    - Primary: DynamoDB (persists across EB deployments, scaling, and restarts)
    - Fallback: SQLite (local dev or when DynamoDB is unavailable)

    On every load, DynamoDB alerts are re-synced into SQLite so the page
    renders correctly even when DynamoDB has records the current instance lacks.
    """
    user_id = str(request.user.id)
    # Fresh instances on every request
    dynamodb_service = _dynamodb()

    # Try DynamoDB — returns [] if unavailable or on any error
    dynamo_alerts = []
    try:
        dynamo_alerts = dynamodb_service.get_user_alerts(
            user_id,
            status='active',
            username=request.user.username,
        )
    except Exception as e:
        logger.warning(f"Could not fetch alerts from DynamoDB: {e}")

    # Also get SQLite alerts for display
    sqlite_alerts = list(Alert.objects.filter(user=request.user).select_related('stock'))
    sqlite_alert_ids = {str(a.id) for a in sqlite_alerts}
    dynamo_alert_ids = {item.get('alert_id', '') for item in dynamo_alerts}

    # Re-sync: bring any DynamoDB alerts not in SQLite back into SQLite
    # This handles: EB restarts, fresh deploys, or DynamoDB-only saves
    newly_synced = 0
    for item in dynamo_alerts:
        item_alert_id = item.get('alert_id', '')
        if item_alert_id not in sqlite_alert_ids:
            try:
                stock_obj, _ = Stock.objects.get_or_create(
                    symbol=item.get('symbol', ''),
                    defaults={'name': item.get('symbol', ''), 'current_price': 0},
                )
                new_alert, created = Alert.objects.get_or_create(
                    user=request.user,
                    stock=stock_obj,
                    condition=item.get('condition', ''),
                    defaults={
                        'threshold': item.get('threshold', 0),
                        'status': item.get('status', 'active'),
                    },
                )
                if created:
                    newly_synced += 1
            except Exception as e:
                logger.warning(f"Could not re-sync alert '{item_alert_id}' from DynamoDB: {e}")

    if newly_synced:
        logger.info(f"Re-synced {newly_synced} alert(s) from DynamoDB into SQLite for user '{request.user.username}'")
        sqlite_alerts = list(Alert.objects.filter(user=request.user).select_related('stock'))

    context = {
        'alerts': sqlite_alerts,
        'dynamo_alerts': dynamo_alerts,
    }
    return render(request, 'stocks/alerts.html', context)



@login_required
def create_alert(request):
    """Create a new stock alert.

    Storage strategy:
    - Primary: DynamoDB (persists across EB deployments and scaling events)
    - Fallback: SQLite (used only when DynamoDB is unavailable, i.e. local dev)
    """
    # Fresh AWS service instances — credentials checked on every alert creation
    dynamodb_service   = _dynamodb()
    sns_service        = _sns()
    cloudwatch_service = _cloudwatch()
    event_producer     = _event_producer()
    lambda_service     = _lambda()

    if request.method == 'POST':
        form = AlertForm(request.POST)
        if form.is_valid():
            symbol = form.cleaned_data['symbol'].upper()
            condition = form.cleaned_data['condition']
            threshold = form.cleaned_data['threshold']

            # Get or create stock
            stock_data = api_service.get_stock_quote(symbol)
            stock_obj, _ = Stock.objects.get_or_create(
                symbol=symbol,
                defaults={
                    'name': stock_data.get('name', symbol),
                    'current_price': Decimal(str(stock_data.get('price', 0))),
                }
            )

            # ── Idempotency: prevent duplicate alerts ─────────────────────
            existing = Alert.objects.filter(
                user=request.user,
                stock=stock_obj,
                condition=condition,
                threshold=threshold,
                status='active',
            ).first()
            if existing:
                messages.info(
                    request,
                    f'You already have an active alert for {symbol} {condition} '
                    f'${float(threshold):.2f}. Duplicate not created.'
                )
                return redirect('alerts')

            # Always create Django model record (needed for page rendering)
            alert = Alert.objects.create(
                user=request.user,
                stock=stock_obj,
                condition=condition,
                threshold=threshold,
            )

            # Use a UUID as the stable DynamoDB key — NOT the SQLite auto-increment id.
            # SQLite ids reset after every EB redeploy; UUIDs are permanent.
            stable_alert_id = str(uuid.uuid4())

            # Primary persistent store: DynamoDB (survives EB redeployments)
            dynamo_ok = dynamodb_service.store_alert_rule(
                alert_id=stable_alert_id,
                user_id=str(request.user.id),
                username=request.user.username,   # fallback for post-restart recovery
                symbol=symbol,
                condition=condition,
                threshold=float(threshold),
            )
            if not dynamo_ok:
                logger.warning(
                    f"Alert for {symbol} could not be saved to DynamoDB. "
                    "Only SQLite copy exists — it will be lost on next EB deploy."
                )

            # Send ALERT_CREATED event to SQS (triggers Lambda pipeline)
            # This completes the event-driven flow: Alert.create -> SQS -> Lambda -> DynamoDB/SNS
            try:
                alert_sqs_sent = event_producer.send_alert_created(
                    alert_id=stable_alert_id,
                    user_id=str(request.user.id),
                    symbol=symbol,
                    condition=condition,
                    threshold=float(threshold),
                )
                if alert_sqs_sent:
                    logger.info(f"[SQS] ALERT_CREATED sent for {symbol} {condition}")
                else:
                    # Fallback: direct Lambda invoke if SQS is unavailable
                    logger.warning(f"[SQS] ALERT_CREATED failed for {symbol} - falling back to direct Lambda invoke")
                    try:
                        lambda_service.invoke_alert_handler(
                            symbol=symbol,
                            price=float(stock_data.get('price', 0)),
                            volume=int(stock_data.get('volume', 0)),
                            change_percent=float(stock_data.get('change_percent', 0)),
                        )
                    except Exception as le:
                        logger.warning(f"[Lambda] invoke_alert_handler failed for {symbol}: {le}")
            except Exception as e:
                logger.warning(f"[SQS/Lambda] Alert pipeline failed for {symbol}: {e}")

            # ── Send alert creation confirmation email ────────────────
            current_price  = float(stock_data.get('price', 0))
            user_email     = request.user.email
            fallback_email = os.getenv('SNS_ALERT_EMAIL', '')
            notify_email   = user_email or fallback_email

            # Subscribe user email (idempotent — safe to call every time)
            subscribe_target = user_email or fallback_email
            if subscribe_target:
                try:
                    sns_service.subscribe(subscribe_target)
                except Exception:
                    pass

            # Human-readable condition explanation
            condition_map = {
                'PRICE_ABOVE':  f'rises ABOVE ${float(threshold):.2f}',
                'PRICE_BELOW':  f'falls BELOW ${float(threshold):.2f}',
                'VOLUME_ABOVE': f'volume exceeds {int(threshold):,}',
                'CHANGE_ABOVE': f'gains more than {float(threshold):.2f}%',
                'CHANGE_BELOW': f'drops more than {abs(float(threshold)):.2f}%',
            }
            condition_text = condition_map.get(condition, f'{condition} {threshold}')

            confirmation_subject = (
                f"Alert Set: {symbol} {condition.replace('_', ' ').title()}"
                f" ${float(threshold):.2f}"
            )
            confirmation_message = (
                f"Hi {request.user.username},\n\n"
                f"Your stock alert has been set successfully.\n"
                f"{'=' * 50}\n\n"
                f"  Stock     : {symbol} ({stock_data.get('name', symbol)})\n"
                f"  Condition : {condition.replace('_', ' ')}\n"
                f"  Threshold : ${float(threshold):.2f}\n"
                f"  Current   : ${current_price:.2f}\n\n"
                f"  You will receive an email when {symbol} {condition_text}.\n\n"
                f"{'=' * 50}\n"
                f"Manage your alerts: https://{request.get_host()}/alerts/\n\n"
                f"-- Cloud Stock Market Analysis Platform"
            )

            ok = sns_service.publish(
                subject=confirmation_subject,
                message=confirmation_message,
            )
            if ok:
                logger.info(
                    f"Alert confirmation published via SNS — "
                    f"symbol={symbol} condition={condition} email={notify_email}"
                )
            else:
                logger.warning(
                    f"SNS publish failed for {symbol} {condition} — "
                    f"alert saved, email notification skipped."
                )

            # Log to CloudWatch
            safe_aws_call(lambda: cloudwatch_service.log_system_event(
                'ALERT_CREATED',
                f"User {request.user.username}: {symbol} {condition} {threshold} "
                f"dynamo={'ok' if dynamo_ok else 'FAILED'}"
            ))

            if ok:
                email_shown = notify_email or 'your registered email'
                messages.success(
                    request,
                    f'✅ Alert created for {symbol}. Confirmation email sent to {email_shown}.'
                )
            else:
                messages.success(
                    request,
                    f'✅ Alert created for {symbol}. '
                    f'Email notification will be sent when the condition is met.'
                )
            return redirect('alerts')
    else:
        form = AlertForm()

    context = {'form': form}
    return render(request, 'stocks/create_alert.html', context)


@login_required
def delete_alert(request, alert_id):
    """Delete a stock alert from both SQLite and DynamoDB."""
    alert = get_object_or_404(Alert, id=alert_id, user=request.user)
    symbol = alert.stock.symbol

    # Remove from DynamoDB first (primary store) — best-effort
    _db = _dynamodb()
    table_name = os.getenv('DYNAMODB_ALERTS_TABLE', 'alert_rules')
    try:
        deleted = _db.delete_item(table_name, {'alert_id': str(alert_id)})
        if deleted:
            logger.info(f"Alert {alert_id} deleted from DynamoDB.")
        else:
            logger.warning(f"Alert {alert_id} could not be deleted from DynamoDB.")
    except Exception as e:
        logger.warning(f"[DynamoDB] delete_alert failed for {alert_id}: {e}")

    # Remove from SQLite (always succeeds regardless of AWS status)
    alert.delete()
    messages.success(request, f'Alert for {symbol} deleted.')
    return redirect('alerts')


# ═══════════════════════════════════════════════════════════════════════
# ANALYTICS
# ═══════════════════════════════════════════════════════════════════════

@login_required
def analytics_view(request):
    """Analytics dashboard — technical indicators and visualizations."""
    symbol = request.GET.get('symbol', 'AAPL').upper()
    # Fresh instances on every request
    s3_service     = _s3()
    event_producer = _event_producer()

    # Fetch data
    stock_data = api_service.get_stock_quote(symbol)
    history = api_service.get_stock_history(symbol, days=90)
    prices = [d['close'] for d in history] if history else []

    # Compute analytics
    analytics = {}
    if prices:
        analytics = indicator_service.compute_indicators(symbol, prices)

    # Store report in S3 (best-effort — never crash the view)
    if analytics:
        try:
            s3_service.upload_json_report(analytics, symbol, 'analytics')
        except Exception as e:
            logger.warning(f"[S3] upload_json_report failed for {symbol}: {e}")

    # Request further processing via SQS (best-effort)
    try:
        event_producer.send_analytics_request(symbol, 'FULL')
    except Exception as e:
        logger.warning(f"[SQS] send_analytics_request failed for {symbol}: {e}")

    # Prepare chart data
    chart_labels = [d['date'] for d in history] if history else []
    chart_prices = prices

    # Compute SMA data for chart
    sma_20 = []
    sma_50 = []
    if len(prices) >= 20:
        from stock_event_engine.indicators import StockAnalyzer
        analyzer = StockAnalyzer(prices, symbol)
        sma_20_raw = analyzer.moving_average(20)
        sma_20 = [None] * 19 + [round(float(v), 2) for v in sma_20_raw]
    if len(prices) >= 50:
        sma_50_raw = analyzer.moving_average(50)
        sma_50 = [None] * 49 + [round(float(v), 2) for v in sma_50_raw]

    context = {
        'symbol': symbol,
        'stock': stock_data,
        'analytics': analytics,
        'indicators': analytics.get('indicators', {}),
        'signals': analytics.get('signals', {}),
        'chart_labels': json.dumps(chart_labels),
        'chart_prices': json.dumps(chart_prices),
        'chart_sma_20': json.dumps(sma_20),
        'chart_sma_50': json.dumps(sma_50),
        'search_form': StockSearchForm(initial={'query': symbol}),
    }
    return render(request, 'stocks/analytics.html', context)


# ═══════════════════════════════════════════════════════════════════════
# AUTHENTICATION
# ═══════════════════════════════════════════════════════════════════════

def register_view(request):
    """User registration."""
    if request.user.is_authenticated:
        return redirect('dashboard')
    sns_service        = _sns()
    cloudwatch_service = _cloudwatch()

    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()

            # Subscribe to SNS for alerts
            email = user.email
            if email:
                sns_service.subscribe(email)

            login(request, user)
            messages.success(request, 'Account created successfully!')

            safe_aws_call(lambda: cloudwatch_service.log_system_event(
                'USER_REGISTERED', f"User: {user.username}"
            ))
            return redirect('dashboard')
    else:
        form = UserRegistrationForm()

    return render(request, 'stocks/register.html', {'form': form})


def login_view(request):
    """User login."""
    if request.user.is_authenticated:
        return redirect('dashboard')
    sns_service        = _sns()
    cloudwatch_service = _cloudwatch()

    if request.method == 'POST':
        username = request.POST.get('username', '')
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)

            # Re-subscribe user's email to SNS on every login.
            # Handles the case where: (a) the initial subscribe on registration
            # failed because credentials were expired at that moment, or (b)
            # the AWS Academy session was refreshed and SNS topic was recreated.
            # AWS SNS subscribe is idempotent — calling it again on an already-
            # confirmed subscription does nothing.
            if user.email and sns_service.available:
                try:
                    sns_service.subscribe(user.email)
                    logger.info(f"SNS subscription refreshed for {user.email}")
                except Exception as e:
                    logger.warning(f"SNS re-subscribe failed for {user.email}: {e}")

            safe_aws_call(lambda: cloudwatch_service.log_system_event(
                'USER_LOGIN', f"User: {username}"
            ))
            next_url = request.GET.get('next', 'dashboard')
            return redirect(next_url)
        else:
            messages.error(request, 'Invalid username or password.')

    return render(request, 'stocks/login.html')


#logout
@login_required
def logout_view(request):
    """User logout."""
    cloudwatch_service = _cloudwatch()
    try:
        cloudwatch_service.log_system_event(
            'USER_LOGOUT', f"User: {request.user.username}"
        )
    except Exception:
        pass
    logout(request)
    messages.info(request, 'You have been logged out.')
    return redirect('dashboard')


@login_required
def profile_view(request):
    """User profile management."""
    profile, _ = UserProfile.objects.get_or_create(
        user=request.user,
        defaults={'role': 'user'}
    )

    if request.method == 'POST':
        form = UserProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, 'Profile updated successfully.')
            return redirect('profile')
    else:
        form = UserProfileForm(instance=profile)

    # Get user stats
    watchlist_count = Watchlist.objects.filter(user=request.user).count()
    alert_count = Alert.objects.filter(user=request.user).count()
    active_alerts = Alert.objects.filter(user=request.user, status='active').count()

    context = {
        'form': form,
        'profile': profile,
        'watchlist_count': watchlist_count,
        'alert_count': alert_count,
        'active_alerts': active_alerts,
    }
    return render(request, 'stocks/profile.html', context)


# ═══════════════════════════════════════════════════════════════════════
# API ENDPOINTS (internal)
# ═══════════════════════════════════════════════════════════════════════

def api_stock_quote(request, symbol):
    """JSON API endpoint for stock quote."""
    data = api_service.get_stock_quote(symbol.upper())
    return JsonResponse(data)

#stock history
def api_stock_history(request, symbol):
    """JSON API endpoint for historical data."""
    days = int(request.GET.get('days', 90))
    history = api_service.get_stock_history(symbol.upper(), days)
    return JsonResponse({'symbol': symbol.upper(), 'history': history})


# ═══════════════════════════════════════════════════════════════════════
# CUSTOM ERROR HANDLERS
# ═══════════════════════════════════════════════════════════════════════

def custom_404(request, exception=None):
    """Friendly 404 page."""
    return render(request, '404.html', status=404)


def custom_500(request):
    """
    Friendly 500 page.

    Shown whenever an unhandled exception reaches Django's WSGI layer.
    Most commonly triggered by expired AWS Academy credentials — the page
    tells users to refresh the lab and try again.
    """
    return render(request, '500.html', status=500)

