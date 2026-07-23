from __future__ import annotations

from django import forms
from django.contrib.auth.forms import UserCreationForm

from accounts.models import User
from billing.models import CustomerProfile


class SignupForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")


class ExchangeCredentialForm(forms.Form):
    EXCHANGE_CHOICES = [("binance", "Binance"), ("bybit", "Bybit")]

    exchange = forms.ChoiceField(choices=EXCHANGE_CHOICES)
    api_key = forms.CharField(widget=forms.PasswordInput)
    api_secret = forms.CharField(widget=forms.PasswordInput)


class SubscribeChoosePlanForm(forms.Form):
    plan_interval = forms.ChoiceField(choices=CustomerProfile.Interval.choices)
    promo_code = forms.CharField(max_length=32, required=False)
