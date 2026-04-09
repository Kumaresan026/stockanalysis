"""
Django models for the stocks app.
Defines UserProfile, Stock, Watchlist, Alert, and AnalyticsResult.
"""

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class UserProfile(models.Model):
    """Extended user profile with role-based access and preferences."""

    ROLE_CHOICES = [
        ('admin', 'Administrator'),
        ('user', 'Regular User'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='user')
    bio = models.TextField(blank=True, default='')
    phone = models.CharField(max_length=20, blank=True, default='')
    notification_email = models.EmailField(blank=True, default='')
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'User Profile'
        verbose_name_plural = 'User Profiles'

    def __str__(self):
        return f"{self.user.username} ({self.role})"

    @property
    def is_admin(self):
        return self.role == 'admin'


class Stock(models.Model):
    """Represents a stock ticker with cached data."""

    symbol = models.CharField(max_length=10, unique=True, db_index=True)
    name = models.CharField(max_length=200, blank=True, default='')
    current_price = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    previous_close = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    volume = models.BigIntegerField(default=0)
    market_cap = models.BigIntegerField(default=0)
    change_percent = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    high_52_week = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    low_52_week = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    last_updated = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['symbol']
        verbose_name = 'Stock'
        verbose_name_plural = 'Stocks'

    def __str__(self):
        return f"{self.symbol} - ${self.current_price}"

    @property
    def change_direction(self):
        if self.change_percent > 0:
            return 'up'
        elif self.change_percent < 0:
            return 'down'
        return 'neutral'


class Watchlist(models.Model):
    """User's watchlist of tracked stocks."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='watchlists')
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE, related_name='watchers')
    added_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True, default='')

    class Meta:
        unique_together = ('user', 'stock')
        ordering = ['-added_at']
        verbose_name = 'Watchlist Entry'
        verbose_name_plural = 'Watchlist Entries'

    def __str__(self):
        return f"{self.user.username} → {self.stock.symbol}"


class Alert(models.Model):
    """User-defined alert rules for stock price monitoring."""

    CONDITION_CHOICES = [
        ('PRICE_ABOVE', 'Price Above'),
        ('PRICE_BELOW', 'Price Below'),
        ('VOLUME_ABOVE', 'Volume Above'),
        ('CHANGE_ABOVE', 'Change % Above'),
        ('CHANGE_BELOW', 'Change % Below'),
    ]

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('triggered', 'Triggered'),
        ('disabled', 'Disabled'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='alerts')
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE, related_name='alerts')
    condition = models.CharField(max_length=20, choices=CONDITION_CHOICES)
    threshold = models.DecimalField(max_digits=12, decimal_places=4)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='active')
    triggered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    message = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Alert'
        verbose_name_plural = 'Alerts'

    def __str__(self):
        return f"{self.stock.symbol} {self.condition} {self.threshold}"


class AnalyticsResult(models.Model):
    """Cached analytics results for a stock."""

    ANALYSIS_TYPES = [
        ('SMA', 'Simple Moving Average'),
        ('EMA', 'Exponential Moving Average'),
        ('VOLATILITY', 'Volatility'),
        ('RSI', 'Relative Strength Index'),
        ('TREND', 'Trend Analysis'),
        ('BREAKOUT', 'Breakout Detection'),
        ('PORTFOLIO', 'Portfolio Analysis'),
    ]

    stock = models.ForeignKey(Stock, on_delete=models.CASCADE, related_name='analytics')
    analysis_type = models.CharField(max_length=20, choices=ANALYSIS_TYPES)
    result_data = models.JSONField(default=dict)
    parameters = models.JSONField(default=dict)
    computed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-computed_at']
        verbose_name = 'Analytics Result'
        verbose_name_plural = 'Analytics Results'

    def __str__(self):
        return f"{self.stock.symbol} - {self.analysis_type} @ {self.computed_at}"
