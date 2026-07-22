"""Dashboard URL routes (PRD §18)."""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard_home, name="dashboard_home"),
    path("discovery/", views.discovery_ranking, name="discovery_ranking"),
    path("wallet/<str:address>/", views.wallet_profile, name="wallet_profile"),
    path("watchlist/", views.watchlist, name="watchlist"),
    path("alerts/", views.alerts_history, name="alerts_history"),
    path("settings/", views.settings_page, name="settings_page"),
    path("whale-copy/", views.whale_copy_status, name="whale_copy_status"),
    path("api/whale-copy/status/", views.whale_copy_api_status, name="whale_copy_api_status"),
]
