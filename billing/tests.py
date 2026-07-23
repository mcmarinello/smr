from django.test import TestCase

from accounts.models import User
from billing.crypto import decrypt_secret, encrypt_secret
from billing.models import CustomerProfile, ExchangeCredential


class CryptoTest(TestCase):
    def test_round_trip(self):
        ciphertext = encrypt_secret("my-api-secret")
        self.assertNotEqual(ciphertext, "my-api-secret")
        self.assertEqual(decrypt_secret(ciphertext), "my-api-secret")


class ExchangeCredentialTest(TestCase):
    def test_secrets_are_never_stored_in_plain_text(self):
        user = User.objects.create_user(username="cliente4", password="x", role=User.Role.CUSTOMER)
        credential = ExchangeCredential(user=user, exchange="binance")
        credential.set_api_key("AKIA-PLAIN-KEY")
        credential.set_api_secret("PLAIN-SECRET")
        credential.save()

        self.assertNotIn("AKIA-PLAIN-KEY", credential.api_key_encrypted)
        self.assertNotIn("PLAIN-SECRET", credential.api_secret_encrypted)
        self.assertEqual(credential.get_api_key(), "AKIA-PLAIN-KEY")
        self.assertEqual(credential.get_api_secret(), "PLAIN-SECRET")


class CustomerProfileTest(TestCase):
    def test_default_status_is_free(self):
        user = User.objects.create_user(username="cliente2", password="x", role=User.Role.CUSTOMER)
        profile = CustomerProfile.objects.create(user=user)
        self.assertEqual(profile.status, CustomerProfile.Status.FREE)
        self.assertFalse(profile.email_verified)
        self.assertIsNone(profile.current_period_end)
        self.assertEqual(user.customer_profile, profile)

    def test_one_profile_per_user(self):
        user = User.objects.create_user(username="cliente3", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user)
        with self.assertRaises(Exception):
            CustomerProfile.objects.create(user=user)
