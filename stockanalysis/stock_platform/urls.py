"""
URL configuration for stock_platform project.
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('stocks.urls')),
]

# Custom error pages — displayed when DEBUG=False (production / EB)
handler404 = 'stocks.views.custom_404'
handler500 = 'stocks.views.custom_500'
