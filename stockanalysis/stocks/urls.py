"""
URL configuration for the stocks app.
"""

from django.urls import path
from stocks import views

urlpatterns = [
    # Dashboard
    path('', views.dashboard, name='dashboard'),

    # Stock views
    path('stock/<str:symbol>/', views.stock_detail, name='stock_detail'),
    path('search/', views.stock_search, name='stock_search'),

    # Watchlist
    path('watchlist/', views.watchlist_view, name='watchlist'),
    path('watchlist/add/<str:symbol>/', views.add_to_watchlist, name='add_to_watchlist'),
    path('watchlist/remove/<str:symbol>/', views.remove_from_watchlist, name='remove_from_watchlist'),

    # Alerts
    path('alerts/', views.alerts_view, name='alerts'),
    path('alerts/create/', views.create_alert, name='create_alert'),
    path('alerts/delete/<int:alert_id>/', views.delete_alert, name='delete_alert'),

    # Analytics
    path('analytics/', views.analytics_view, name='analytics'),

    # Authentication
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.profile_view, name='profile'),

    # API endpoints
    path('api/quote/<str:symbol>/', views.api_stock_quote, name='api_stock_quote'),
    path('api/history/<str:symbol>/', views.api_stock_history, name='api_stock_history'),
]
