"""
Django views for the stocks app.

Implements all user-facing views: dashboard, stock detail, search,
watchlist, alerts, analytics, and authentication flows.
All views integrate with AWS services via the service layer.
"""

import json
import logging
import os
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

# Initialize services
api_service = StockAPIService()
dynamodb_service = DynamoDBService()
s3_service = S3Service()
sqs_service = SQSService()
sns_service = SNSService()
cloudwatch_service = CloudWatchService()
event_producer = StockEventProducer()
indicator_service = StockIndicatorService()
lambda_service = LambdaService()


# ═══════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════════

def dashboard(request):
    """Main dashboard — market overview with top gainers/losers."""
    from stocks.services.aws_session import check_aws_available

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

    # Log dashboard access to CloudWatch
    cloudwatch_service.log_system_event(
        'DASHBOARD_ACCESS',
        f"User: {request.user.username if request.user.is_authenticated else 'anonymous'}"
    )

    # Get user's watchlist if authenticated
    watchlist_stocks = []
    if request.user.is_authenticated:
        watchlist_entries = Watchlist.objects.filter(user=request.user).select_related('stock')[:5]
        for entry in watchlist_entries:
            data = api_service.get_stock_quote(entry.stock.symbol)
            watchlist_stocks.append(data)

    # Evaluate alerts for all stocks shown on the dashboard
    # This ensures alerts fire even if the user never visits a stock's detail page.
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

    context = {
        'gainers': gainers,
        'losers': losers,
        'watchlist_stocks': watchlist_stocks,
        'search_form': StockSearchForm(),
        'aws_connected': check_aws_available(),
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

    # Step 1: Fetch real-time data
    stock_data = api_service.get_stock_quote(symbol)
    history = api_service.get_stock_history(symbol, days=90)

    # Step 2: Store in DynamoDB via service layer
    dynamodb_service.store_stock_data(
        symbol=symbol,
        price=stock_data.get('price', 0),
        volume=stock_data.get('volume', 0),
        change_percent=stock_data.get('change_percent', 0),
    )

    # Step 3: Evaluate alerts directly (in-process, no Lambda dependency)
    # This fires every time any user views this stock's detail page.
    evaluate_and_notify(
        symbol=symbol,
        price=stock_data.get('price', 0),
        volume=stock_data.get('volume', 0),
        change_percent=stock_data.get('change_percent', 0),
        dynamodb_service=dynamodb_service,
        sns_service=sns_service,
    )

    # Step 4: Also invoke Lambda asynchronously (if deployed) for analytics pipeline
    # This is fire-and-forget — failure here does NOT affect alert evaluation above
    lambda_service.invoke_stock_processor(
        symbol=symbol,
        price=stock_data.get('price', 0),
        volume=stock_data.get('volume', 0),
        change_percent=stock_data.get('change_percent', 0),
    )

    # Log stock fetch to CloudWatch
    cloudwatch_service.log_stock_fetch(
        symbol, stock_data.get('source', 'unknown'), True
    )

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

    if query:
        results = api_service.search_stocks(query)
        cloudwatch_service.log_system_event(
            'STOCK_SEARCH', f"Query: {query}, Results: {len(results)}"
        )

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

    # Store in DynamoDB
    table_name = os.getenv('DYNAMODB_WATCHLIST_TABLE', 'user_watchlists')
    dynamodb_service.put_item(
        table_name,
        {
            'user_id': str(request.user.id),
            'symbol': symbol,
            'added_at': datetime.utcnow().isoformat(),
        }
    )

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

    # Try DynamoDB first — filtered by both user_id and username (fallback)
    dynamo_alerts = []
    if dynamodb_service.available:
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
        'aws_connected': dynamodb_service.available,
    }
    return render(request, 'stocks/alerts.html', context)



@login_required
def create_alert(request):
    """Create a new stock alert.

    Storage strategy:
    - Primary: DynamoDB (persists across EB deployments and scaling events)
    - Fallback: SQLite (used only when DynamoDB is unavailable, i.e. local dev)
    """
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

            # Invoke alert_handler Lambda directly for immediate evaluation
            lambda_service.invoke_alert_handler(
                symbol=symbol,
                price=float(stock_data.get('price', 0)),
                volume=int(stock_data.get('volume', 0)),
                change_percent=float(stock_data.get('change_percent', 0)),
            )

            # ── Send alert creation confirmation email ────────────────
            # Re-subscribe on every alert creation to ensure the email
            # is always subscribed even if the initial subscribe failed.
            current_price = float(stock_data.get('price', 0))
            user_email    = request.user.email

            if sns_service.available:
                # If user has an email on their account, subscribe it to SNS.
                # AWS SNS subscribe is idempotent — safe to call every time.
                if user_email:
                    try:
                        sns_service.subscribe(user_email)
                    except Exception:
                        pass  # best-effort — publish still goes to confirmed subscribers

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
                    f"Your stock alert has been set successfully!\n"
                    f"{'=' * 50}\n\n"
                    f"  Stock     : {symbol} ({stock_data.get('name', symbol)})\n"
                    f"  Condition : {condition.replace('_', ' ')}\n"
                    f"  Threshold : ${float(threshold):.2f}\n"
                    f"  Current   : ${current_price:.2f}\n\n"
                    f"  You will receive an email when {symbol} {condition_text}.\n\n"
                    f"{'=' * 50}\n"
                    f"Manage your alerts: https://{request.get_host()}/alerts/\n\n"
                    f"-- Cloud Stock Market Analysis Platform\n"
                    f"   Powered by AWS SNS"
                )

                ok = sns_service.publish(
                    subject=confirmation_subject,
                    message=confirmation_message,
                )
                if ok:
                    logger.info(f"Alert confirmation email sent to {user_email} for {symbol} {condition}")
                else:
                    logger.warning(f"Alert confirmation email FAILED for {user_email}. "
                                   "Check: SNS topic exists, email subscription confirmed.")
            elif not user_email:
                logger.warning(f"User {request.user.username} has no email — cannot send alert confirmation.")

            # Log to CloudWatch
            cloudwatch_service.log_system_event(
                'ALERT_CREATED',
                f"User {request.user.username}: {symbol} {condition} {threshold} "
                f"dynamo={'ok' if dynamo_ok else 'FAILED'}"
            )

            messages.success(request, f'Alert created for {symbol}. Check your email for confirmation!')
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

    # Remove from DynamoDB first (primary store)
    if dynamodb_service.available:
        table_name = os.getenv('DYNAMODB_ALERTS_TABLE', 'alert_rules')
        deleted = dynamodb_service.delete_item(table_name, {'alert_id': str(alert_id)})
        if deleted:
            logger.info(f"Alert {alert_id} deleted from DynamoDB.")
        else:
            logger.warning(f"Alert {alert_id} could not be deleted from DynamoDB.")

    # Remove from SQLite
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

    # Fetch data
    stock_data = api_service.get_stock_quote(symbol)
    history = api_service.get_stock_history(symbol, days=90)
    prices = [d['close'] for d in history] if history else []

    # Compute analytics
    analytics = {}
    if prices:
        analytics = indicator_service.compute_indicators(symbol, prices)

    # Store report in S3
    if analytics:
        s3_service.upload_json_report(analytics, symbol, 'analytics')

    # Request further processing via SQS
    event_producer.send_analytics_request(symbol, 'FULL')

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

            cloudwatch_service.log_system_event(
                'USER_REGISTERED', f"User: {user.username}"
            )
            return redirect('dashboard')
    else:
        form = UserRegistrationForm()

    return render(request, 'stocks/register.html', {'form': form})


def login_view(request):
    """User login."""
    if request.user.is_authenticated:
        return redirect('dashboard')

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

            cloudwatch_service.log_system_event(
                'USER_LOGIN', f"User: {username}"
            )
            next_url = request.GET.get('next', 'dashboard')
            return redirect(next_url)
        else:
            messages.error(request, 'Invalid username or password.')

    return render(request, 'stocks/login.html')


@login_required
def logout_view(request):
    """User logout."""
    cloudwatch_service.log_system_event(
        'USER_LOGOUT', f"User: {request.user.username}"
    )
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


def api_stock_history(request, symbol):
    """JSON API endpoint for historical data."""
    days = int(request.GET.get('days', 90))
    history = api_service.get_stock_history(symbol.upper(), days)
    return JsonResponse({'symbol': symbol.upper(), 'history': history})
