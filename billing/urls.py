from django.urls import path

from billing import views

app_name = "billing"

urlpatterns = [
    path("signup/", views.SignupView.as_view(), name="signup"),
    path("signup/confirme-seu-email/", views.VerifyEmailSentView.as_view(), name="verify_email_sent"),
    path("verificar-email/<str:uidb64>/<str:token>/", views.VerifyEmailView.as_view(), name="verify_email"),
    path("minhas-credenciais/", views._GatedProbeView.as_view(), name="gated_probe"),
    path("assine/", views.SubscribeRequiredView.as_view(), name="subscribe_required"),
]
