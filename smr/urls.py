from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from django.shortcuts import render

def landing_page(request):
    return render(request, "landing/landing.html")

urlpatterns = [
    path("", landing_page, name="landing"),
    path("app/", include("dashboard.urls")),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("billing.urls")),
    path("admin/", admin.site.urls),
    path("api/bridge/", include("bridge.urls")),
    path("", include("monitoring.urls")),
]
