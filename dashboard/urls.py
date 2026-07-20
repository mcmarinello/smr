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
]