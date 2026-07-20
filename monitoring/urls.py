"""Monitoring URL routes (PRD Sprint 10 §Observability)."""

from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health_check, name="health_check"),
]
