from __future__ import annotations

from django.contrib.auth.tokens import PasswordResetTokenGenerator


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    """
    Same trick Django uses for password-reset tokens: the hash embeds
    mutable state (here, `email_verified`) so a token stops validating the
    moment it has been used once.
    """

    def _make_hash_value(self, user, timestamp):
        profile = getattr(user, "customer_profile", None)
        email_verified = profile.email_verified if profile else False
        return f"{user.pk}{timestamp}{email_verified}"


email_verification_token = EmailVerificationTokenGenerator()
