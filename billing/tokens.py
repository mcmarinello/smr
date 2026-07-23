from __future__ import annotations

from django.contrib.auth.tokens import PasswordResetTokenGenerator


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    """
    Same trick Django uses for password-reset tokens: the hash embeds
    mutable state (here, `email_verified`) so a token stops validating the
    moment it has been used once.
    """

    def _make_hash_value(self, user, timestamp):
        return f"{user.pk}{timestamp}{user.customer_profile.email_verified}"


email_verification_token = EmailVerificationTokenGenerator()
