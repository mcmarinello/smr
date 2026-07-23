from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views.generic import FormView, TemplateView
from django.views.generic import View as GenericView

from accounts.models import User
from billing.emails import send_verification_email
from billing.forms import ExchangeCredentialForm, SignupForm, SubscribeChoosePlanForm
from billing.mixins import SubscriptionRequiredMixin
from billing.models import CryptoPayment, CustomerProfile, ExchangeCredential, Favorite, PromoCode
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


class VerifyEmailView(GenericView):
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


class SubscribeChoosePlanView(LoginRequiredMixin, FormView):
    template_name = "registration/subscribe_choose_plan.html"
    form_class = SubscribeChoosePlanForm

    def form_valid(self, form):
        plan_interval = form.cleaned_data["plan_interval"]
        promo_input = form.cleaned_data["promo_code"]

        if plan_interval == CustomerProfile.Interval.MONTHLY:
            amount = Decimal(str(settings.TRC20_MONTHLY_PRICE_USDT))
        else:
            amount = Decimal(str(settings.TRC20_ANNUAL_PRICE_USDT))

        promo = None
        if promo_input:
            promo = PromoCode.objects.filter(code=promo_input).first()
            if promo is None or not promo.is_valid():
                form.add_error("promo_code", "Código promocional inválido ou expirado.")
                return self.form_invalid(form)
            amount = amount * (Decimal(100 - promo.discount_percent) / Decimal(100))

        payment = CryptoPayment.objects.create(
            user=self.request.user,
            plan_interval=plan_interval,
            expected_amount_usdt=amount,
            promo_code=promo,
            expires_at=timezone.now() + timedelta(minutes=settings.TRC20_PAYMENT_EXPIRY_MINUTES),
        )
        self.success_url = reverse("billing:crypto_payment_detail", kwargs={"pk": payment.pk})
        return super().form_valid(form)
