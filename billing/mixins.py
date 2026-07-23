from __future__ import annotations

from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect

from billing.access import access_redirect


class SubscriptionRequiredMixin:
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        target = access_redirect(request.user)
        if target:
            return redirect(target)
        return super().dispatch(request, *args, **kwargs)
