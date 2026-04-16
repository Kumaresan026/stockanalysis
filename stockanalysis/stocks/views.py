"""
Django views for the stocks app.

Production-resilient implementation:
- All AWS calls are fire-and-forget (daemon threads) — never delay HTTP response
- All DB operations wrapped in try/except — graceful fallback on any failure
- No CloudWatch in views — use Django logger only (CloudWatch removed per spec)
- All Decimal conversions use _safe_decimal() — never crash on None/bad values
- Every view returns a meaningful response even with AWS completely offline
"""

import json
import logging
import os
import threading
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation

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
from stocks.services.alert_evaluator import evaluate_and_notify
from stocks.events.producer import StockEventProducer
from stocks.analytics.indicators import StockIndicatorService
from stocks.services.lambda_service import LambdaService

logger = logging.getLogger('stocks')

# Non-AWS services — safe to initialize once at startup
api_service       = StockAPIService()
indicator_service = StockIndicatorService()


# ── Decimal helper ────────────────────────────────────────────────────────────

def _safe_decimal(value, default=0):
    """
    Safely convert any value to Decimal.
    Returns Decimal(default) if value is None, empty, or unconvertible.
    """
    try:
        if value is None:
            return Decimal(str(default))
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(str(default))


# ── Per-request AWS service factories ────────────────────────────────────────
# ALWAYS call inside view/function bodies — NEVER at module level.

def _sns():            return SNSService()
def _dynamodb():       return DynamoDBService()
def _s3():             return S3Service()
def _sqs():            return SQSService()
def _event_producer(): return StockEventProducer()
def _lambda():         return LambdaService()


# ── Safe AWS execution helpers ────────────────────────────────────────────────

def safe_aws_call(func, default=None):
    """
    Execute an AWS call, silently returning ``default`` on any exception.
    Ensures AWS failures NEVER propagate to Django views as HTTP 500s.
    """
    try:
        return func()
    except Exception as exc:
        logger.warning("[AWS] safe_aws_call suppressed %s: %s", type(exc).__name__, exc)
        return default


def fire_and_forget(func):
    """
    Run an AWS operation on a background daemon thread.
    HTTP response is returned immediately; cloud write completes (or fails) silently.
    """
    def _worker():
        try:
            func()
        except Exception as exc:
            logger.warning("[AWS] background task suppressed %s: %s", type(exc).__name__, exc)
    threading.Thread(target=_worker, daemon=True).start()


# ═══════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════════

def dashboard(request):
    """Main dashboard — market overview with top gainers/losers."""
    dynamodb_service = _dynamodb()
    sns_service      = _sns()

    # Fetch top movers
    movers  = api_service.get_top_movers(count=6)
    gainers = movers.get('gainers', [])
    losers  = movers.get('losers', [])

    # Fallback: if no API key, fetch known symbols directly for display
    if not gainers and not losers:
        popular    = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'TSLA', 'META', 'NFLX']
        all_quotes = [api_service.get_stock_quote(sym) for sym in popular]
        gainers    = [q for q in all_quotes if q.get('change_percent', 0) >= 0][:4]
        losers     = [q for q in all_quotes if q.get('change_percent', 0) < 0][:4]
        if not losers:
            gainers, losers = all_quotes[:4], all_quotes[4:]

    # Get user's watchlist if authenticated
    watchlist_stocks = []
    if request.user.is_authenticated:
        try:
            watchlist_entries = Watchlist.objects.filter(
                user=request.user
            ).select_related('stock')[:5]
            for entry in watchlist_entries:
                data = api_service.get_stock_quote(entry.stock.symbol)
                watchlist_stocks.append(data)
        except Exception as e:
            logger.warning("[Dashboard] watchlist fetch failed: %s", e)

    # Evaluate alerts — best-effort, never crash
    try:
        seen_symbols = set()
        for stock_data in gainers + losers + watchlist_stocks:
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
    except Exception as e:
        logger.warning("[Dashboard] alert evaluation failed: %s", e)

    context = {
        'gainers':          gainers,
        'losers':           losers,
        'watchlist_stocks': watchlist_stocks,
        'search_form':      StockSearchForm(),
    }
    return render(request, 'stocks/dashboard.html', context)


# ═══════════════════════════════════════════════════════════════════════
# STOCK VIEWS
# ═══════════════════════════════════════════════════════════════════════

def stock_detail(request, symbol):
    """
    Stock detail page — price, volume, historical chart, analytics.
    All AWS writes are fire-and-forget. All DB ops are wrapped in try/except.
    This view NEVER returns 500.
    """
    symbol     = symbol.upper()
    stock_data = api_service.get_stock_quote(symbol)
    history    = api_service.get_stock_history(symbol, days=90)

    # Scalar captures for thread-safe lambdas
    _price   = float(stock_data.get('price') or 0)
    _volume  = int(stock_data.get('volume') or 0)
    _chg_pct = float(stock_data.get('change_percent') or 0)

    # Background AWS fire-and-forget writes
    fire_and_forget(lambda: _dynamodb().store_stock_data(
        symbol=symbol, price=_price, volume=_volume, change_percent=_chg_pct
    ))
    fire_and_forget(lambda: evaluate_and_notify(
        symbol=symbol, price=_price, volume=_volume, change_percent=_chg_pct,
        dynamodb_service=_dynamodb(), sns_service=_sns(),
    ))
    fire_and_forget(lambda: safe_aws_call(
        lambda: _event_producer().send_stock_update(
            symbol=symbol, price=_price, volume=_volume, change_percent=_chg_pct
        )
    ))

    # Compute analytics (pure Python — no AWS)
    prices    = [d['close'] for d in history] if history else []
    analytics = {}
    if prices:
        try:
            analytics = indicator_service.compute_indicators(symbol, prices)
        except Exception as e:
            logger.warning("[Analytics] compute_indicators failed for %s: %s", symbol, e)

    # Update / create Django model — wrapped so Decimal errors don't 500
    stock_obj = None
    try:
        stock_obj, _ = Stock.objects.update_or_create(
            symbol=symbol,
            defaults={
                'name':           stock_data.get('name', symbol),
                'current_price':  _safe_decimal(stock_data.get('price'), 0),
                'previous_close': _safe_decimal(stock_data.get('previous_close'), 0),
                'volume':         _volume,
                'change_percent': _safe_decimal(stock_data.get('change_percent'), 0),
                'last_updated':   timezone.now(),
            }
        )
    except Exception as e:
        logger.warning("[DB] update_or_create Stock failed for %s: %s", symbol, e)
        # Fallback: get existing or build minimal in-memory stub
        try:
            stock_obj = Stock.objects.get(symbol=symbol)
        except Stock.DoesNotExist:
            stock_obj = Stock(symbol=symbol, name=stock_data.get('name', symbol),
                              current_price=_safe_decimal(stock_data.get('price'), 0))
        except Exception:
            stock_obj = Stock(symbol=symbol, name=symbol, current_price=Decimal('0'))

    # Check watchlist membership
    in_watchlist = False
    if request.user.is_authenticated and stock_obj and stock_obj.pk:
        try:
            in_watchlist = Watchlist.objects.filter(
                user=request.user, stock=stock_obj
            ).exists()
        except Exception as e:
            logger.warning("[DB] watchlist check failed: %s", e)

    chart_labels  = [d['date']   for d in history] if history else []
    chart_prices  = [d['close']  for d in history] if history else []
    chart_volumes = [d['volume'] for d in history] if history else []

    context = {
        'stock':        stock_data,
        'stock_obj':    stock_obj,
        'history':      history,
        'analytics':    analytics,
        'in_watchlist': in_watchlist,
        'chart_labels':  json.dumps(chart_labels),
        'chart_prices':  json.dumps(chart_prices),
        'chart_volumes': json.dumps(chart_volumes),
        'indicators':    analytics.get('indicators', {}),
        'signals':       analytics.get('signals', {}),
    }
    return render(request, 'stocks/stock_detail.html', context)


def stock_search(request):
    """Search for stocks by symbol or name."""
    query   = request.GET.get('query', '').strip()
    results = []

    if query:
        try:
            results = api_service.search_stocks(query)
        except Exception as e:
            logger.warning("[Search] search_stocks failed for '%s': %s", query, e)

    context = {
        'query':       query,
        'results':     results,
        'search_form': StockSearchForm(initial={'query': query}),
    }
    return render(request, 'stocks/stock_search.html', context)


# ═══════════════════════════════════════════════════════════════════════
# WATCHLIST
# ═══════════════════════════════════════════════════════════════════════

@login_required
def watchlist_view(request):
    """Display user's watchlist with live prices."""
    watchlist_data = []
    try:
        entries = Watchlist.objects.filter(user=request.user).select_related('stock')
        for entry in entries:
            try:
                quote = api_service.get_stock_quote(entry.stock.symbol)
            except Exception:
                quote = {'symbol': entry.stock.symbol, 'price': 0}
            watchlist_data.append({'entry': entry, 'quote': quote})
    except Exception as e:
        logger.warning("[Watchlist] fetch failed: %s", e)

    context = {
        'watchlist_data': watchlist_data,
        'form':           WatchlistForm(),
    }
    return render(request, 'stocks/watchlist.html', context)


@login_required
def add_to_watchlist(request, symbol):
    """Add a stock to the user's watchlist."""
    symbol     = symbol.upper()
    stock_data = api_service.get_stock_quote(symbol)

    try:
        stock_obj, _ = Stock.objects.get_or_create(
            symbol=symbol,
            defaults={
                'name':          stock_data.get('name', symbol),
                'current_price': _safe_decimal(stock_data.get('price'), 0),
            }
        )
        _, created = Watchlist.objects.get_or_create(
            user=request.user, stock=stock_obj,
        )
    except Exception as e:
        logger.warning("[Watchlist] add_to_watchlist DB failed for %s: %s", symbol, e)
        messages.error(request, f'Could not add {symbol} to watchlist. Please try again.')
        return redirect('stock_detail', symbol=symbol)

    # DynamoDB best-effort
    fire_and_forget(lambda: _dynamodb().put_item(
        os.getenv('DYNAMODB_WATCHLIST_TABLE', 'user_watchlists'),
        {'user_id': str(request.user.id), 'symbol': symbol,
         'added_at': datetime.utcnow().isoformat()}
    ))

    if created:
        messages.success(request, f'{symbol} added to your watchlist.')
    else:
        messages.info(request, f'{symbol} is already in your watchlist.')
    return redirect('stock_detail', symbol=symbol)


@login_required
def remove_from_watchlist(request, symbol):
    """Remove a stock from the user's watchlist."""
    symbol = symbol.upper()
    try:
        Watchlist.objects.filter(user=request.user, stock__symbol=symbol).delete()
        messages.success(request, f'{symbol} removed from your watchlist.')
    except Exception as e:
        logger.warning("[Watchlist] remove failed for %s: %s", symbol, e)
        messages.error(request, 'Could not remove from watchlist.')
    return redirect('watchlist')


# ═══════════════════════════════════════════════════════════════════════
# ALERTS
# ═══════════════════════════════════════════════════════════════════════

@login_required
def alerts_view(request):
    """
    Display user's alerts.
    Primary store: DynamoDB. Fallback: SQLite.
    Gracefully degrades if DynamoDB is unavailable.
    """
    user_id          = str(request.user.id)
    dynamodb_service = _dynamodb()

    # Try DynamoDB — returns [] on any error
    dynamo_alerts = []
    try:
        dynamo_alerts = dynamodb_service.get_user_alerts(
            user_id, status='active', username=request.user.username,
        )
    except Exception as e:
        logger.warning("[Alerts] DynamoDB fetch failed: %s", e)

    # SQLite alerts
    sqlite_alerts    = []
    sqlite_alert_ids = set()
    try:
        sqlite_alerts    = list(Alert.objects.filter(user=request.user).select_related('stock'))
        sqlite_alert_ids = {str(a.id) for a in sqlite_alerts}
    except Exception as e:
        logger.warning("[Alerts] SQLite fetch failed: %s", e)

    # Re-sync DynamoDB → SQLite (best-effort)
    newly_synced = 0
    for item in dynamo_alerts:
        item_alert_id = item.get('alert_id', '')
        if item_alert_id not in sqlite_alert_ids:
            try:
                stock_obj, _ = Stock.objects.get_or_create(
                    symbol=item.get('symbol', ''),
                    defaults={'name': item.get('symbol', ''), 'current_price': 0},
                )
                _, created = Alert.objects.get_or_create(
                    user=request.user,
                    stock=stock_obj,
                    condition=item.get('condition', ''),
                    defaults={
                        'threshold': item.get('threshold', 0),
                        'status':    item.get('status', 'active'),
                    },
                )
                if created:
                    newly_synced += 1
            except Exception as e:
                logger.warning("[Alerts] sync failed for '%s': %s", item_alert_id, e)

    if newly_synced:
        try:
            sqlite_alerts = list(Alert.objects.filter(user=request.user).select_related('stock'))
        except Exception:
            pass

    context = {
        'alerts':        sqlite_alerts,
        'dynamo_alerts': dynamo_alerts,
    }
    return render(request, 'stocks/alerts.html', context)


@login_required
def create_alert(request):
    """
    Create a new stock alert.
    Primary store: DynamoDB. Fallback: SQLite.
    """
    dynamodb_service = _dynamodb()
    sns_service      = _sns()

    if request.method == 'POST':
        form = AlertForm(request.POST)
        if form.is_valid():
            symbol    = form.cleaned_data['symbol'].upper()
            condition = form.cleaned_data['condition']
            threshold = form.cleaned_data['threshold']

            # Get or create stock
            stock_data = api_service.get_stock_quote(symbol)
            try:
                stock_obj, _ = Stock.objects.get_or_create(
                    symbol=symbol,
                    defaults={
                        'name':          stock_data.get('name', symbol),
                        'current_price': _safe_decimal(stock_data.get('price'), 0),
                    }
                )
            except Exception as e:
                logger.warning("[CreateAlert] Stock get_or_create failed: %s", e)
                messages.error(request, 'Could not create alert. Please try again.')
                return render(request, 'stocks/create_alert.html', {'form': form})

            # Idempotency check
            try:
                existing = Alert.objects.filter(
                    user=request.user, stock=stock_obj,
                    condition=condition, threshold=threshold, status='active',
                ).first()
                if existing:
                    messages.info(
                        request,
                        f'You already have an active alert for {symbol} {condition} '
                        f'${float(threshold):.2f}.'
                    )
                    return redirect('alerts')
            except Exception as e:
                logger.warning("[CreateAlert] idempotency check failed: %s", e)

            # Create SQLite record
            try:
                alert = Alert.objects.create(
                    user=request.user, stock=stock_obj,
                    condition=condition, threshold=threshold,
                )
            except Exception as e:
                logger.warning("[CreateAlert] SQLite create failed: %s", e)
                messages.error(request, 'Could not save alert. Please try again.')
                return render(request, 'stocks/create_alert.html', {'form': form})

            stable_alert_id = str(uuid.uuid4())

            # DynamoDB primary store (best-effort)
            dynamo_ok = safe_aws_call(
                lambda: dynamodb_service.store_alert_rule(
                    alert_id=stable_alert_id,
                    user_id=str(request.user.id),
                    username=request.user.username,
                    symbol=symbol,
                    condition=condition,
                    threshold=float(threshold),
                ),
                default=False
            )
            if not dynamo_ok:
                logger.warning("[CreateAlert] DynamoDB store failed for %s — SQLite only.", symbol)

            # SQS pipeline (fire-and-forget)
            _price   = float(stock_data.get('price') or 0)
            _volume  = int(stock_data.get('volume') or 0)
            _chg_pct = float(stock_data.get('change_percent') or 0)

            fire_and_forget(lambda: safe_aws_call(
                lambda: _event_producer().send_alert_created(
                    alert_id=stable_alert_id,
                    user_id=str(request.user.id),
                    symbol=symbol, condition=condition,
                    threshold=float(threshold),
                )
            ))

            # SNS confirmation email (best-effort)
            user_email     = request.user.email
            fallback_email = os.getenv('SNS_ALERT_EMAIL', '')
            notify_email   = user_email or fallback_email

            if notify_email:
                safe_aws_call(lambda: sns_service.subscribe(notify_email))

            condition_map = {
                'PRICE_ABOVE':  f'rises ABOVE ${float(threshold):.2f}',
                'PRICE_BELOW':  f'falls BELOW ${float(threshold):.2f}',
                'VOLUME_ABOVE': f'volume exceeds {int(threshold):,}',
                'CHANGE_ABOVE': f'gains more than {float(threshold):.2f}%',
                'CHANGE_BELOW': f'drops more than {abs(float(threshold)):.2f}%',
            }
            condition_text = condition_map.get(condition, f'{condition} {threshold}')

            email_ok = safe_aws_call(
                lambda: sns_service.publish(
                    subject=f"Alert Set: {symbol} {condition.replace('_', ' ').title()} ${float(threshold):.2f}",
                    message=(
                        f"Hi {request.user.username},\n\n"
                        f"Your stock alert has been set successfully.\n"
                        f"{'=' * 50}\n\n"
                        f"  Stock     : {symbol} ({stock_data.get('name', symbol)})\n"
                        f"  Condition : {condition.replace('_', ' ')}\n"
                        f"  Threshold : ${float(threshold):.2f}\n"
                        f"  Current   : ${_price:.2f}\n\n"
                        f"  You will be alerted when {symbol} {condition_text}.\n\n"
                        f"{'=' * 50}\n"
                        f"Manage your alerts: https://{request.get_host()}/alerts/\n\n"
                        f"-- Cloud Stock Market Analysis Platform"
                    ),
                ),
                default=False
            )

            if email_ok:
                messages.success(
                    request,
                    f'✅ Alert created for {symbol}. Confirmation email sent to {notify_email or "your email"}.'
                )
            else:
                messages.success(
                    request,
                    f'✅ Alert created for {symbol}. '
                    f'You will be notified when the condition is met.'
                )
            return redirect('alerts')
    else:
        form = AlertForm()

    return render(request, 'stocks/create_alert.html', {'form': form})


@login_required
def delete_alert(request, alert_id):
    """Delete a stock alert from both SQLite and DynamoDB."""
    try:
        alert  = get_object_or_404(Alert, id=alert_id, user=request.user)
        symbol = alert.stock.symbol
    except Exception as e:
        logger.warning("[DeleteAlert] get_object_or_404 failed: %s", e)
        return redirect('alerts')

    # DynamoDB delete (best-effort)
    fire_and_forget(lambda: _dynamodb().delete_item(
        os.getenv('DYNAMODB_ALERTS_TABLE', 'alert_rules'),
        {'alert_id': str(alert_id)}
    ))

    # SQLite delete
    try:
        alert.delete()
        messages.success(request, f'Alert for {symbol} deleted.')
    except Exception as e:
        logger.warning("[DeleteAlert] SQLite delete failed: %s", e)
        messages.error(request, 'Could not delete alert.')

    return redirect('alerts')


# ═══════════════════════════════════════════════════════════════════════
# ANALYTICS
# ═══════════════════════════════════════════════════════════════════════

@login_required
def analytics_view(request):
    """Analytics dashboard — technical indicators and visualizations."""
    symbol     = request.GET.get('symbol', 'AAPL').upper()
    stock_data = api_service.get_stock_quote(symbol)
    history    = api_service.get_stock_history(symbol, days=90)
    prices     = [d['close'] for d in history] if history else []

    analytics = {}
    if prices:
        try:
            analytics = indicator_service.compute_indicators(symbol, prices)
        except Exception as e:
            logger.warning("[Analytics] compute_indicators failed: %s", e)

    # S3 report (fire-and-forget)
    if analytics:
        fire_and_forget(lambda: safe_aws_call(
            lambda: _s3().upload_json_report(analytics, symbol, 'analytics')
        ))

    # SQS analytics request (fire-and-forget)
    fire_and_forget(lambda: safe_aws_call(
        lambda: _event_producer().send_analytics_request(symbol, 'FULL')
    ))

    chart_labels = [d['date']  for d in history] if history else []
    chart_prices = prices
    sma_20 = []
    sma_50 = []

    if len(prices) >= 20:
        try:
            from stock_event_engine.indicators import StockAnalyzer
            analyzer    = StockAnalyzer(prices, symbol)
            sma_20_raw  = analyzer.moving_average(20)
            sma_20      = [None] * 19 + [round(float(v), 2) for v in sma_20_raw]
            if len(prices) >= 50:
                sma_50_raw = analyzer.moving_average(50)
                sma_50     = [None] * 49 + [round(float(v), 2) for v in sma_50_raw]
        except Exception as e:
            logger.warning("[Analytics] StockAnalyzer failed: %s", e)

    context = {
        'symbol':        symbol,
        'stock':         stock_data,
        'analytics':     analytics,
        'indicators':    analytics.get('indicators', {}),
        'signals':       analytics.get('signals', {}),
        'chart_labels':  json.dumps(chart_labels),
        'chart_prices':  json.dumps(chart_prices),
        'chart_sma_20':  json.dumps(sma_20),
        'chart_sma_50':  json.dumps(sma_50),
        'search_form':   StockSearchForm(initial={'query': symbol}),
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
            try:
                user = form.save()
            except Exception as e:
                logger.error("[Register] form.save() failed: %s", e)
                messages.error(request, 'Registration failed. Please try again.')
                return render(request, 'stocks/register.html', {'form': form})

            # SNS subscribe — best-effort
            if user.email:
                fire_and_forget(lambda: safe_aws_call(
                    lambda: _sns().subscribe(user.email)
                ))

            login(request, user)
            messages.success(request, 'Account created successfully!')
            logger.info("[Auth] User registered: %s", user.username)
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

        try:
            user = authenticate(request, username=username, password=password)
        except Exception as e:
            logger.error("[Login] authenticate() failed: %s", e)
            messages.error(request, 'Login failed. Please try again.')
            return render(request, 'stocks/login.html')

        if user is not None:
            login(request, user)

            # SNS re-subscribe on every login (idempotent) — fire-and-forget
            if user.email:
                fire_and_forget(lambda: safe_aws_call(
                    lambda: _sns().subscribe(user.email)
                ))

            logger.info("[Auth] User logged in: %s", username)
            next_url = request.GET.get('next', 'dashboard')
            return redirect(next_url)
        else:
            messages.error(request, 'Invalid username or password.')

    return render(request, 'stocks/login.html')


@login_required
def logout_view(request):
    """User logout."""
    username = request.user.username
    logout(request)
    logger.info("[Auth] User logged out: %s", username)
    messages.info(request, 'You have been logged out.')
    return redirect('dashboard')


@login_required
def profile_view(request):
    """User profile management."""
    try:
        profile, _ = UserProfile.objects.get_or_create(
            user=request.user, defaults={'role': 'user'}
        )
    except Exception as e:
        logger.warning("[Profile] get_or_create UserProfile failed: %s", e)
        profile = UserProfile(user=request.user, role='user')

    if request.method == 'POST':
        form = UserProfileForm(request.POST, instance=profile)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, 'Profile updated successfully.')
            except Exception as e:
                logger.warning("[Profile] save failed: %s", e)
                messages.error(request, 'Could not save profile.')
            return redirect('profile')
    else:
        form = UserProfileForm(instance=profile)

    watchlist_count = 0
    alert_count     = 0
    active_alerts   = 0
    try:
        watchlist_count = Watchlist.objects.filter(user=request.user).count()
        alert_count     = Alert.objects.filter(user=request.user).count()
        active_alerts   = Alert.objects.filter(user=request.user, status='active').count()
    except Exception as e:
        logger.warning("[Profile] stats query failed: %s", e)

    context = {
        'form':            form,
        'profile':         profile,
        'watchlist_count': watchlist_count,
        'alert_count':     alert_count,
        'active_alerts':   active_alerts,
    }
    return render(request, 'stocks/profile.html', context)


# ═══════════════════════════════════════════════════════════════════════
# API ENDPOINTS (internal)
# ═══════════════════════════════════════════════════════════════════════

def api_stock_quote(request, symbol):
    """JSON API endpoint for stock quote."""
    try:
        data = api_service.get_stock_quote(symbol.upper())
    except Exception as e:
        logger.warning("[API] get_stock_quote failed: %s", e)
        data = {'symbol': symbol.upper(), 'error': str(e)}
    return JsonResponse(data)


def api_stock_history(request, symbol):
    """JSON API endpoint for historical data."""
    try:
        days    = int(request.GET.get('days', 90))
        history = api_service.get_stock_history(symbol.upper(), days)
    except Exception as e:
        logger.warning("[API] get_stock_history failed: %s", e)
        history = []
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
    Should be very rarely shown now that all views are exception-safe.
    """
    return render(request, '500.html', status=500)
