from __future__ import annotations

from django.contrib.auth import login
from django.db import transaction
from django.shortcuts import render
from django.urls import reverse_lazy
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views import View
from django.views.generic import FormView, TemplateView

from accounts.models import User
from billing.emails import send_verification_email
from billing.forms import SignupForm
from billing.models import CustomerProfile
from billing.tokens import email_verification_token


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
