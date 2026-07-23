from datetime import timedelta

from django.core import mail
from django.db import IntegrityError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models import User
from billing.access import access_redirect
from billing.crypto import decrypt_secret, encrypt_secret
from billing.emails import send_verification_email
from billing.models import CustomerProfile, ExchangeCredential, Favorite
from billing.tasks import expire_subscriptions
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

    def test_staff_user_uidb64_does_not_crash(self):
        staff = User.objects.create_user(username="staffnoprofile", password="x", role=User.Role.VIEWER)
        uidb64 = urlsafe_base64_encode(force_bytes(staff.pk))
        response = self.client.get(reverse("billing:verify_email", kwargs={"uidb64": uidb64, "token": "qualquer-coisa"}))
        self.assertEqual(response.status_code, 400)


class AccessRedirectTest(TestCase):
    def test_staff_always_allowed(self):
        user = User.objects.create_user(username="staff2", password="x", role=User.Role.OPERATOR)
        self.assertIsNone(access_redirect(user))

    def test_free_customer_is_redirected_to_subscribe(self):
        user = User.objects.create_user(username="cliente12", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user, status=CustomerProfile.Status.FREE)
        self.assertEqual(access_redirect(user), "billing:subscribe_required")

    def test_active_customer_is_allowed(self):
        user = User.objects.create_user(username="cliente13", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user, status=CustomerProfile.Status.ACTIVE)
        self.assertIsNone(access_redirect(user))

    @override_settings(EMAIL_VERIFICATION_REQUIRED=True)
    def test_unverified_active_customer_is_redirected_to_verify(self):
        user = User.objects.create_user(username="cliente14", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user, status=CustomerProfile.Status.ACTIVE, email_verified=False)
        self.assertEqual(access_redirect(user), "billing:verify_email_sent")

    def test_customer_without_profile_is_redirected_not_crashed(self):
        user = User.objects.create_user(username="clientesemperfil", password="x", role=User.Role.CUSTOMER)
        self.assertEqual(access_redirect(user), "billing:subscribe_required")


class SubscriptionGatingViewTest(TestCase):
    def test_mixin_blocks_free_customer(self):
        user = User.objects.create_user(username="cliente15", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user, status=CustomerProfile.Status.FREE)
        self.client.force_login(user)
        response = self.client.get("/minhas-credenciais/")
        self.assertRedirects(response, reverse("billing:subscribe_required"))

    def test_mixin_allows_active_customer(self):
        user = User.objects.create_user(username="cliente16", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user, status=CustomerProfile.Status.ACTIVE)
        self.client.force_login(user)
        response = self.client.get("/minhas-credenciais/")
        self.assertEqual(response.status_code, 200)

    def test_mixin_allows_staff_regardless_of_profile(self):
        user = User.objects.create_user(username="staff3", password="x", role=User.Role.ADMIN)
        self.client.force_login(user)
        response = self.client.get("/minhas-credenciais/")
        self.assertEqual(response.status_code, 200)

    def test_mixin_redirects_anonymous_to_login(self):
        response = self.client.get("/minhas-credenciais/")
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)


class ExchangeCredentialCreateViewTest(TestCase):
    def test_creates_encrypted_credential(self):
        user = User.objects.create_user(username="cliente20", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user, status=CustomerProfile.Status.ACTIVE)
        self.client.force_login(user)

        response = self.client.post(
            reverse("billing:exchange_credential_create"),
            {"exchange": "binance", "api_key": "AKIA-PLAIN", "api_secret": "SECRET-PLAIN"},
        )

        self.assertEqual(response.status_code, 302)
        credential = ExchangeCredential.objects.get(user=user)
        self.assertEqual(credential.exchange, "binance")
        self.assertNotIn("AKIA-PLAIN", credential.api_key_encrypted)
        self.assertEqual(credential.get_api_key(), "AKIA-PLAIN")


class ExpireSubscriptionsTaskTest(TestCase):
    def test_expires_active_profiles_past_period_end(self):
        user = User.objects.create_user(username="cliente17", password="x", role=User.Role.CUSTOMER)
        profile = CustomerProfile.objects.create(
            user=user,
            status=CustomerProfile.Status.ACTIVE,
            current_period_end=timezone.now() - timedelta(days=1),
        )

        count = expire_subscriptions()

        profile.refresh_from_db()
        self.assertEqual(count, 1)
        self.assertEqual(profile.status, CustomerProfile.Status.EXPIRED)

    def test_leaves_active_profiles_with_future_period_end_untouched(self):
        user = User.objects.create_user(username="cliente18", password="x", role=User.Role.CUSTOMER)
        profile = CustomerProfile.objects.create(
            user=user,
            status=CustomerProfile.Status.ACTIVE,
            current_period_end=timezone.now() + timedelta(days=1),
        )

        expire_subscriptions()

        profile.refresh_from_db()
        self.assertEqual(profile.status, CustomerProfile.Status.ACTIVE)

    def test_leaves_free_profiles_untouched(self):
        user = User.objects.create_user(username="cliente19", password="x", role=User.Role.CUSTOMER)
        profile = CustomerProfile.objects.create(user=user, status=CustomerProfile.Status.FREE)

        expire_subscriptions()

        profile.refresh_from_db()
        self.assertEqual(profile.status, CustomerProfile.Status.FREE)


class FavoriteToggleViewTest(TestCase):
    def test_toggling_on_then_off(self):
        user = User.objects.create_user(username="cliente21", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user, status=CustomerProfile.Status.ACTIVE)
        wallet = Wallet.objects.create(address="0x" + "b" * 40)
        self.client.force_login(user)
        url = reverse("billing:favorite_toggle", kwargs={"wallet_id": wallet.pk})

        first = self.client.post(url)
        self.assertEqual(first.json(), {"favorited": True})
        self.assertTrue(Favorite.objects.filter(user=user, wallet=wallet).exists())

        second = self.client.post(url)
        self.assertEqual(second.json(), {"favorited": False})
        self.assertFalse(Favorite.objects.filter(user=user, wallet=wallet).exists())

    def test_requires_active_subscription(self):
        user = User.objects.create_user(username="cliente22", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user, status=CustomerProfile.Status.FREE)
        wallet = Wallet.objects.create(address="0x" + "c" * 40)
        self.client.force_login(user)

        response = self.client.post(reverse("billing:favorite_toggle", kwargs={"wallet_id": wallet.pk}))

        self.assertRedirects(response, reverse("billing:subscribe_required"))
