from __future__ import annotations

from django.conf import settings

from accounts.models import User
from billing.models import CustomerProfile


def access_redirect(user) -> str | None:
    """
    Returns the URL *name* to redirect a customer to if they should not see
    a gated view, or None if access is allowed. Staff roles never gated.
    """
    if user.role != User.Role.CUSTOMER:
        return None

    try:
        profile = user.customer_profile
    except CustomerProfile.DoesNotExist:
        return "billing:subscribe_required"
    if settings.EMAIL_VERIFICATION_REQUIRED and not profile.email_verified:
        return "billing:verify_email_sent"
    if profile.status != CustomerProfile.Status.ACTIVE:
        return "billing:subscribe_required"
    return None


def has_full_access(user) -> bool:
    """True for staff roles and active-subscription customers alike."""
    return access_redirect(user) is None
