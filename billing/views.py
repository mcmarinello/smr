from __future__ import annotations

from django.contrib.auth import login
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views import View
from django.views.generic import FormView, TemplateView
from django.views.generic import View as GenericView

from accounts.models import User
from billing.emails import send_verification_email
from billing.forms import ExchangeCredentialForm, SignupForm
from billing.mixins import SubscriptionRequiredMixin
from billing.models import CustomerProfile, ExchangeCredential, Favorite
from billing.tokens import email_verification_token
from wallets.models import Wallet


class SignupView(FormView):
    template_name = "registration/signup.html"
    form_class = SignupForm
    success_url = reverse_lazy("billing:verify_email_sent")

    def form_valid(self, form):
        with transaction.atomic():
            user = form.save(commit=False)
            user.role = User.Role.CUSTOMER
            user.save()
            CustomerProfile.objects.create(user=user)
        send_verification_email(user, self.request)
        login(self.request, user)
        return super().form_valid(form)


class VerifyEmailSentView(TemplateView):
    template_name = "registration/verify_email_sent.html"


class VerifyEmailView(View):
    def get(self, request, uidb64, token):
        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)
        except (User.DoesNotExist, ValueError, TypeError, OverflowError):
            user = None

        if user is not None and email_verification_token.check_token(user, token):
            user.customer_profile.email_verified = True
            user.customer_profile.save(update_fields=["email_verified"])
            return render(request, "registration/verify_email_result.html", {"success": True})
        return render(request, "registration/verify_email_result.html", {"success": False}, status=400)


class ExchangeCredentialCreateView(SubscriptionRequiredMixin, FormView):
    template_name = "registration/exchange_credential_form.html"
    form_class = ExchangeCredentialForm
    success_url = reverse_lazy("dashboard_home")

    def form_valid(self, form):
        credential = ExchangeCredential(user=self.request.user, exchange=form.cleaned_data["exchange"])
        credential.set_api_key(form.cleaned_data["api_key"])
        credential.set_api_secret(form.cleaned_data["api_secret"])
        credential.save()
        return super().form_valid(form)


class SubscribeRequiredView(TemplateView):
    template_name = "registration/subscribe_required.html"


class FavoriteToggleView(SubscriptionRequiredMixin, GenericView):
    def post(self, request, wallet_id):
        wallet = get_object_or_404(Wallet, pk=wallet_id)
        favorite, created = Favorite.objects.get_or_create(user=request.user, wallet=wallet)
        if not created:
            favorite.delete()
            return JsonResponse({"favorited": False})
        return JsonResponse({"favorited": True})
