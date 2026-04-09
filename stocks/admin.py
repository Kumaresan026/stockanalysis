"""
Django admin configuration for the stocks app.
"""

from django.contrib import admin
from stocks.models import UserProfile, Stock, Watchlist, Alert, AnalyticsResult


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'phone', 'created_at')
    list_filter = ('role',)
    search_fields = ('user__username', 'user__email')


@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ('symbol', 'name', 'current_price', 'change_percent', 'volume', 'last_updated')
    list_filter = ('last_updated',)
    search_fields = ('symbol', 'name')
    ordering = ('symbol',)


@admin.register(Watchlist)
class WatchlistAdmin(admin.ModelAdmin):
    list_display = ('user', 'stock', 'added_at')
    list_filter = ('added_at',)
    search_fields = ('user__username', 'stock__symbol')


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ('user', 'stock', 'condition', 'threshold', 'status', 'created_at')
    list_filter = ('status', 'condition')
    search_fields = ('user__username', 'stock__symbol')


@admin.register(AnalyticsResult)
class AnalyticsResultAdmin(admin.ModelAdmin):
    list_display = ('stock', 'analysis_type', 'computed_at')
    list_filter = ('analysis_type',)
    search_fields = ('stock__symbol',)
