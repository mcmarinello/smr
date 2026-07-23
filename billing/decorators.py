from __future__ import annotations

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect

from billing.access import access_redirect


def subscription_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        target = access_redirect(request.user)
        if target:
            return redirect(target)
        return view_func(request, *args, **kwargs)

    return wrapped
