from django.urls import path

from billing import views

app_name = "billing"

urlpatterns = [
    path("signup/", views.SignupView.as_view(), name="signup"),
]
