from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/bridge/", include("bridge.urls")),
    path("", include("monitoring.urls")),
    path("", include("dashboard.urls")),
]
