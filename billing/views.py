from __future__ import annotations

from django.contrib.auth import login
from django.db import transaction
from django.urls import reverse_lazy
from django.views.generic import FormView

from accounts.models import User
from billing.forms import SignupForm
from billing.models import CustomerProfile


class SignupView(FormView):
    template_name = "registration/signup.html"
    form_class = SignupForm
    success_url = reverse_lazy("dashboard_home")

    def form_valid(self, form):
        with transaction.atomic():
            user = form.save(commit=False)
            user.role = User.Role.CUSTOMER
            user.save()
            CustomerProfile.objects.create(user=user)
        login(self.request, user)
        return super().form_valid(form)
