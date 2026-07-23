from django.core import mail
from django.db import IntegrityError
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models import User
from billing.crypto import decrypt_secret, encrypt_secret
from billing.emails import send_verification_email
from billing.models import CustomerProfile, ExchangeCredential, Favorite
from billing.tokens import email_verification_token
from wallets.models import Wallet


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


class FavoriteTest(TestCase):
    def test_unique_per_user_and_wallet(self):
        user = User.objects.create_user(username="cliente5", password="x", role=User.Role.CUSTOMER)
        wallet = Wallet.objects.create(address="0x" + "a" * 40)
        Favorite.objects.create(user=user, wallet=wallet)
        with self.assertRaises(IntegrityError):
            Favorite.objects.create(user=user, wallet=wallet)


class SignupViewTest(TestCase):
    def test_signup_creates_user_and_free_profile(self):
        response = self.client.post(
            reverse("billing:signup"),
            {
                "username": "novocliente",
                "email": "novo@example.com",
                "password1": "S3nhaForte!23",
                "password2": "S3nhaForte!23",
            },
        )
        user = User.objects.get(username="novocliente")
        self.assertEqual(user.role, User.Role.CUSTOMER)
        self.assertEqual(user.customer_profile.status, CustomerProfile.Status.FREE)
        self.assertRedirects(response, reverse("billing:verify_email_sent"))

    def test_signup_logs_the_user_in(self):
        self.client.post(
            reverse("billing:signup"),
            {
                "username": "cliente6",
                "email": "cliente6@example.com",
                "password1": "S3nhaForte!23",
                "password2": "S3nhaForte!23",
            },
        )
        response = self.client.get(reverse("dashboard_home"))
        self.assertEqual(response.wsgi_request.user.username, "cliente6")


class EmailVerificationTokenTest(TestCase):
    def test_token_is_valid_for_unverified_user(self):
        user = User.objects.create_user(username="cliente7", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user)
        token = email_verification_token.make_token(user)
        self.assertTrue(email_verification_token.check_token(user, token))

    def test_token_is_invalid_after_verification(self):
        user = User.objects.create_user(username="cliente8", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user)
        token = email_verification_token.make_token(user)
        user.customer_profile.email_verified = True
        user.customer_profile.save(update_fields=["email_verified"])
        self.assertFalse(email_verification_token.check_token(user, token))


class SendVerificationEmailTest(TestCase):
    def test_sends_one_email_with_a_working_link(self):
        user = User.objects.create_user(username="cliente9", password="x", role=User.Role.CUSTOMER, email="cliente9@example.com")
        CustomerProfile.objects.create(user=user)
        request = RequestFactory().get("/")
        send_verification_email(user, request)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["cliente9@example.com"])


class VerifyEmailViewTest(TestCase):
    def test_valid_token_marks_email_verified(self):
        user = User.objects.create_user(username="cliente10", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user)
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
        token = email_verification_token.make_token(user)

        response = self.client.get(reverse("billing:verify_email", kwargs={"uidb64": uidb64, "token": token}))

        self.assertEqual(response.status_code, 200)
        user.customer_profile.refresh_from_db()
        self.assertTrue(user.customer_profile.email_verified)

    def test_invalid_token_does_not_verify(self):
        user = User.objects.create_user(username="cliente11", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user)
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))

        response = self.client.get(reverse("billing:verify_email", kwargs={"uidb64": uidb64, "token": "lixo-invalido"}))

        self.assertEqual(response.status_code, 400)
        user.customer_profile.refresh_from_db()
        self.assertFalse(user.customer_profile.email_verified)
