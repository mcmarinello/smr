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


class CryptoPaymentVerifyForm(forms.Form):
    tx_hash = forms.CharField(max_length=64, required=False)
    screenshot = forms.ImageField(required=False)

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("tx_hash") and not cleaned_data.get("screenshot"):
            raise forms.ValidationError("Informe o hash da transação ou envie um print.")
        return cleaned_data
