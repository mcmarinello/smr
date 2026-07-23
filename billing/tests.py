from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

import httpx
from PIL import Image as PILImage
from django.conf import settings
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
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
from billing.models import CryptoPayment, CustomerProfile, ExchangeCredential, Favorite, PromoCode
from billing.ocr import extract_tx_hash
from billing.tasks import expire_crypto_payments, expire_subscriptions
from billing.tokens import email_verification_token
from billing.tron import TronVerificationError, verify_transaction
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


class ExpireCryptoPaymentsTaskTest(TestCase):
    def test_expires_pending_payments_past_expiry(self):
        user = User.objects.create_user(username="expira1", password="x", role=User.Role.CUSTOMER)
        payment = CryptoPayment.objects.create(
            user=user,
            plan_interval=CustomerProfile.Interval.MONTHLY,
            expected_amount_usdt=Decimal("10.00"),
            expires_at=timezone.now() - timedelta(minutes=1),
        )

        count = expire_crypto_payments()

        payment.refresh_from_db()
        self.assertEqual(count, 1)
        self.assertEqual(payment.status, CryptoPayment.Status.EXPIRED)

    def test_leaves_pending_payments_within_window_untouched(self):
        user = User.objects.create_user(username="expira2", password="x", role=User.Role.CUSTOMER)
        payment = CryptoPayment.objects.create(
            user=user,
            plan_interval=CustomerProfile.Interval.MONTHLY,
            expected_amount_usdt=Decimal("10.00"),
            expires_at=timezone.now() + timedelta(minutes=10),
        )

        expire_crypto_payments()

        payment.refresh_from_db()
        self.assertEqual(payment.status, CryptoPayment.Status.PENDING)

    def test_leaves_confirmed_payments_untouched(self):
        user = User.objects.create_user(username="expira3", password="x", role=User.Role.CUSTOMER)
        payment = CryptoPayment.objects.create(
            user=user,
            plan_interval=CustomerProfile.Interval.MONTHLY,
            expected_amount_usdt=Decimal("10.00"),
            expires_at=timezone.now() - timedelta(minutes=1),
            status=CryptoPayment.Status.CONFIRMED,
            tx_hash="e" * 64,
        )

        expire_crypto_payments()

        payment.refresh_from_db()
        self.assertEqual(payment.status, CryptoPayment.Status.CONFIRMED)


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


class PromoCodeTest(TestCase):
    def test_valid_code_passes(self):
        promo = PromoCode.objects.create(code="PROMO50", discount_percent=50)
        self.assertTrue(promo.is_valid())

    def test_inactive_code_is_invalid(self):
        promo = PromoCode.objects.create(code="OFF", discount_percent=10, is_active=False)
        self.assertFalse(promo.is_valid())

    def test_expired_code_is_invalid(self):
        promo = PromoCode.objects.create(
            code="EXPIRED10",
            discount_percent=10,
            valid_until=timezone.now() - timedelta(days=1),
        )
        self.assertFalse(promo.is_valid())

    def test_code_at_max_uses_is_invalid(self):
        promo = PromoCode.objects.create(code="LIMITED", discount_percent=10, max_uses=1, uses_count=1)
        self.assertFalse(promo.is_valid())

    def test_code_with_no_max_uses_is_always_valid_by_use_count(self):
        promo = PromoCode.objects.create(code="UNLIMITED", discount_percent=10, uses_count=1000)
        self.assertTrue(promo.is_valid())


class CryptoPaymentTest(TestCase):
    def test_default_status_is_pending(self):
        user = User.objects.create_user(username="cripto1", password="x", role=User.Role.CUSTOMER)
        payment = CryptoPayment.objects.create(
            user=user,
            plan_interval=CustomerProfile.Interval.MONTHLY,
            expected_amount_usdt=Decimal("10.00"),
            expires_at=timezone.now() + timedelta(minutes=30),
        )
        self.assertEqual(payment.status, CryptoPayment.Status.PENDING)
        self.assertIsNone(payment.tx_hash)

    def test_tx_hash_unique_across_payments(self):
        user = User.objects.create_user(username="cripto2", password="x", role=User.Role.CUSTOMER)
        CryptoPayment.objects.create(
            user=user,
            plan_interval=CustomerProfile.Interval.MONTHLY,
            expected_amount_usdt=Decimal("10.00"),
            expires_at=timezone.now() + timedelta(minutes=30),
            tx_hash="a" * 64,
            status=CryptoPayment.Status.CONFIRMED,
        )
        with self.assertRaises(IntegrityError):
            CryptoPayment.objects.create(
                user=user,
                plan_interval=CustomerProfile.Interval.MONTHLY,
                expected_amount_usdt=Decimal("10.00"),
                expires_at=timezone.now() + timedelta(minutes=30),
                tx_hash="a" * 64,
                status=CryptoPayment.Status.CONFIRMED,
            )

    def test_multiple_pending_payments_can_have_null_tx_hash(self):
        user = User.objects.create_user(username="cripto3", password="x", role=User.Role.CUSTOMER)
        CryptoPayment.objects.create(
            user=user,
            plan_interval=CustomerProfile.Interval.MONTHLY,
            expected_amount_usdt=Decimal("10.00"),
            expires_at=timezone.now() + timedelta(minutes=30),
        )
        CryptoPayment.objects.create(
            user=user,
            plan_interval=CustomerProfile.Interval.ANNUAL,
            expected_amount_usdt=Decimal("100.00"),
            expires_at=timezone.now() + timedelta(minutes=30),
        )
        self.assertEqual(CryptoPayment.objects.filter(user=user).count(), 2)


class VerifyTransactionTest(TestCase):
    @patch("billing.tron.httpx.get")
    def test_valid_transfer_returns_amount(self, mock_get):
        mock_get.return_value.json.return_value = {
            "data": [
                {
                    "event_name": "Transfer",
                    "contract_address": settings.TRC20_USDT_CONTRACT_ADDRESS,
                    "result": {"from": "TSender111", "to": settings.TRC20_WALLET_ADDRESS, "value": "10000000"},
                }
            ]
        }
        mock_get.return_value.raise_for_status.return_value = None
        amount = verify_transaction("f" * 64, Decimal("10.00"))
        self.assertEqual(amount, Decimal("10.00"))

    @patch("billing.tron.httpx.get")
    def test_no_matching_event_raises(self, mock_get):
        mock_get.return_value.json.return_value = {"data": []}
        mock_get.return_value.raise_for_status.return_value = None
        with self.assertRaises(TronVerificationError):
            verify_transaction("g" * 64, Decimal("10.00"))

    @patch("billing.tron.httpx.get")
    def test_wrong_recipient_raises(self, mock_get):
        mock_get.return_value.json.return_value = {
            "data": [
                {
                    "event_name": "Transfer",
                    "contract_address": settings.TRC20_USDT_CONTRACT_ADDRESS,
                    "result": {"from": "TSender111", "to": "TOutraCarteira999", "value": "10000000"},
                }
            ]
        }
        mock_get.return_value.raise_for_status.return_value = None
        with self.assertRaises(TronVerificationError):
            verify_transaction("h" * 64, Decimal("10.00"))

    @patch("billing.tron.httpx.get")
    def test_wrong_contract_raises(self, mock_get):
        mock_get.return_value.json.return_value = {
            "data": [
                {
                    "event_name": "Transfer",
                    "contract_address": "TOutroContrato999",
                    "result": {"from": "TSender111", "to": settings.TRC20_WALLET_ADDRESS, "value": "10000000"},
                }
            ]
        }
        mock_get.return_value.raise_for_status.return_value = None
        with self.assertRaises(TronVerificationError):
            verify_transaction("i" * 64, Decimal("10.00"))

    @patch("billing.tron.httpx.get")
    def test_underpaid_raises(self, mock_get):
        mock_get.return_value.json.return_value = {
            "data": [
                {
                    "event_name": "Transfer",
                    "contract_address": settings.TRC20_USDT_CONTRACT_ADDRESS,
                    "result": {"from": "TSender111", "to": settings.TRC20_WALLET_ADDRESS, "value": "5000000"},
                }
            ]
        }
        mock_get.return_value.raise_for_status.return_value = None
        with self.assertRaises(TronVerificationError):
            verify_transaction("j" * 64, Decimal("10.00"))

    @patch("billing.tron.httpx.get")
    def test_overpaid_is_accepted(self, mock_get):
        mock_get.return_value.json.return_value = {
            "data": [
                {
                    "event_name": "Transfer",
                    "contract_address": settings.TRC20_USDT_CONTRACT_ADDRESS,
                    "result": {"from": "TSender111", "to": settings.TRC20_WALLET_ADDRESS, "value": "15000000"},
                }
            ]
        }
        mock_get.return_value.raise_for_status.return_value = None
        amount = verify_transaction("k" * 64, Decimal("10.00"))
        self.assertEqual(amount, Decimal("15.00"))

    @patch("billing.tron.httpx.get")
    def test_network_failure_raises_tron_verification_error(self, mock_get):
        mock_get.side_effect = httpx.ConnectError("connection refused")
        with self.assertRaises(TronVerificationError):
            verify_transaction("l" * 64, Decimal("10.00"))


class SubscribeChoosePlanViewTest(TestCase):
    def test_creates_pending_payment_without_promo(self):
        user = User.objects.create_user(username="assinante1", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user)
        self.client.force_login(user)

        response = self.client.post(
            reverse("billing:subscribe_choose_plan"),
            {"plan_interval": CustomerProfile.Interval.MONTHLY, "promo_code": ""},
        )

        payment = CryptoPayment.objects.get(user=user)
        self.assertEqual(payment.expected_amount_usdt, Decimal(str(settings.TRC20_MONTHLY_PRICE_USDT)))
        self.assertEqual(payment.status, CryptoPayment.Status.PENDING)
        self.assertRedirects(response, reverse("billing:crypto_payment_detail", kwargs={"pk": payment.pk}))

    def test_applies_valid_promo_code_discount(self):
        user = User.objects.create_user(username="assinante2", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user)
        PromoCode.objects.create(code="METADE", discount_percent=50)
        self.client.force_login(user)

        self.client.post(
            reverse("billing:subscribe_choose_plan"),
            {"plan_interval": CustomerProfile.Interval.MONTHLY, "promo_code": "METADE"},
        )

        payment = CryptoPayment.objects.get(user=user)
        expected = Decimal(str(settings.TRC20_MONTHLY_PRICE_USDT)) * Decimal("0.5")
        self.assertEqual(payment.expected_amount_usdt, expected)
        self.assertEqual(payment.promo_code.code, "METADE")

    def test_rejects_invalid_promo_code(self):
        user = User.objects.create_user(username="assinante3", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user)
        self.client.force_login(user)

        response = self.client.post(
            reverse("billing:subscribe_choose_plan"),
            {"plan_interval": CustomerProfile.Interval.MONTHLY, "promo_code": "NAOEXISTE"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CryptoPayment.objects.filter(user=user).exists())

    def test_requires_login(self):
        response = self.client.post(
            reverse("billing:subscribe_choose_plan"),
            {"plan_interval": CustomerProfile.Interval.MONTHLY, "promo_code": ""},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)


class ExtractTxHashTest(TestCase):
    def _fake_image_bytes(self) -> BytesIO:
        buffer = BytesIO()
        PILImage.new("RGB", (10, 10)).save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    @patch("billing.ocr.pytesseract.image_to_string")
    def test_extracts_valid_hash_from_text(self, mock_ocr):
        mock_ocr.return_value = f"Transaction Hash: {'a' * 64}\nStatus: Confirmed"
        result = extract_tx_hash(self._fake_image_bytes())
        self.assertEqual(result, "a" * 64)

    @patch("billing.ocr.pytesseract.image_to_string")
    def test_returns_none_when_no_hash_found(self, mock_ocr):
        mock_ocr.return_value = "blurry unreadable text"
        result = extract_tx_hash(self._fake_image_bytes())
        self.assertIsNone(result)


class CryptoPaymentDetailViewTest(TestCase):
    def _create_payment(self, user, amount="10.00"):
        return CryptoPayment.objects.create(
            user=user,
            plan_interval=CustomerProfile.Interval.MONTHLY,
            expected_amount_usdt=Decimal(amount),
            expires_at=timezone.now() + timedelta(minutes=30),
        )

    def test_get_shows_address_and_amount(self):
        user = User.objects.create_user(username="pagador1", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user)
        payment = self._create_payment(user)
        self.client.force_login(user)

        response = self.client.get(reverse("billing:crypto_payment_detail", kwargs={"pk": payment.pk}))

        self.assertContains(response, "10.00")
        self.assertContains(response, settings.TRC20_WALLET_ADDRESS)

    @patch("billing.views.verify_transaction")
    def test_valid_hash_activates_subscription(self, mock_verify):
        mock_verify.return_value = Decimal("10.00")
        user = User.objects.create_user(username="pagador2", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user)
        payment = self._create_payment(user)
        self.client.force_login(user)

        response = self.client.post(
            reverse("billing:crypto_payment_detail", kwargs={"pk": payment.pk}),
            {"tx_hash": "a" * 64},
        )

        payment.refresh_from_db()
        user.customer_profile.refresh_from_db()
        self.assertEqual(payment.status, CryptoPayment.Status.CONFIRMED)
        self.assertEqual(user.customer_profile.status, CustomerProfile.Status.ACTIVE)
        self.assertEqual(user.customer_profile.plan_interval, CustomerProfile.Interval.MONTHLY)
        self.assertRedirects(response, reverse("dashboard_home"))

    @patch("billing.views.verify_transaction")
    def test_invalid_hash_shows_error_and_does_not_activate(self, mock_verify):
        mock_verify.side_effect = TronVerificationError(
            "Transação não encontrada ou ainda não confirmada. Tente novamente em alguns segundos."
        )
        user = User.objects.create_user(username="pagador3", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user)
        payment = self._create_payment(user)
        self.client.force_login(user)

        response = self.client.post(
            reverse("billing:crypto_payment_detail", kwargs={"pk": payment.pk}),
            {"tx_hash": "b" * 64},
        )

        payment.refresh_from_db()
        self.assertEqual(payment.status, CryptoPayment.Status.PENDING)
        self.assertContains(response, "Transação não encontrada")

    @patch("billing.views.verify_transaction")
    @patch("billing.views.extract_tx_hash")
    def test_screenshot_extracts_hash_via_ocr(self, mock_extract, mock_verify):
        mock_extract.return_value = "c" * 64
        mock_verify.return_value = Decimal("10.00")
        user = User.objects.create_user(username="pagador4", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user)
        payment = self._create_payment(user)
        self.client.force_login(user)

        screenshot = SimpleUploadedFile("print.png", self._fake_png_bytes(), content_type="image/png")
        response = self.client.post(
            reverse("billing:crypto_payment_detail", kwargs={"pk": payment.pk}),
            {"tx_hash": "", "screenshot": screenshot},
        )

        payment.refresh_from_db()
        self.assertEqual(payment.status, CryptoPayment.Status.CONFIRMED)
        self.assertEqual(payment.tx_hash, "c" * 64)

    @patch("billing.views.extract_tx_hash")
    def test_ocr_failure_asks_for_manual_hash(self, mock_extract):
        mock_extract.return_value = None
        user = User.objects.create_user(username="pagador5", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user)
        payment = self._create_payment(user)
        self.client.force_login(user)

        screenshot = SimpleUploadedFile("print.png", self._fake_png_bytes(), content_type="image/png")
        response = self.client.post(
            reverse("billing:crypto_payment_detail", kwargs={"pk": payment.pk}),
            {"tx_hash": "", "screenshot": screenshot},
        )

        payment.refresh_from_db()
        self.assertEqual(payment.status, CryptoPayment.Status.PENDING)
        self.assertContains(response, "cole o hash da transação manualmente")

    @patch("billing.views.verify_transaction")
    def test_reused_hash_is_rejected(self, mock_verify):
        mock_verify.return_value = Decimal("10.00")
        user = User.objects.create_user(username="pagador6", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user)
        CryptoPayment.objects.create(
            user=user,
            plan_interval=CustomerProfile.Interval.MONTHLY,
            expected_amount_usdt=Decimal("10.00"),
            expires_at=timezone.now() + timedelta(minutes=30),
            tx_hash="d" * 64,
            status=CryptoPayment.Status.CONFIRMED,
        )
        payment = self._create_payment(user)
        self.client.force_login(user)

        response = self.client.post(
            reverse("billing:crypto_payment_detail", kwargs={"pk": payment.pk}),
            {"tx_hash": "d" * 64},
        )

        payment.refresh_from_db()
        self.assertEqual(payment.status, CryptoPayment.Status.PENDING)
        self.assertContains(response, "já foi usada")
        mock_verify.assert_not_called()

    def test_cannot_access_another_users_payment(self):
        owner = User.objects.create_user(username="dono", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=owner)
        payment = self._create_payment(owner)
        intruder = User.objects.create_user(username="intruso", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=intruder)
        self.client.force_login(intruder)

        response = self.client.get(reverse("billing:crypto_payment_detail", kwargs={"pk": payment.pk}))

        self.assertEqual(response.status_code, 404)

    @patch("billing.views.verify_transaction")
    def test_reused_hash_is_rejected_regardless_of_case(self, mock_verify):
        mock_verify.return_value = Decimal("10.00")
        user = User.objects.create_user(username="pagador7", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user)
        CryptoPayment.objects.create(
            user=user,
            plan_interval=CustomerProfile.Interval.MONTHLY,
            expected_amount_usdt=Decimal("10.00"),
            expires_at=timezone.now() + timedelta(minutes=30),
            tx_hash="f" * 64,
            status=CryptoPayment.Status.CONFIRMED,
        )
        payment = self._create_payment(user)
        self.client.force_login(user)

        response = self.client.post(
            reverse("billing:crypto_payment_detail", kwargs={"pk": payment.pk}),
            {"tx_hash": "F" * 64},
        )

        payment.refresh_from_db()
        self.assertEqual(payment.status, CryptoPayment.Status.PENDING)
        self.assertContains(response, "já foi usada")
        mock_verify.assert_not_called()

    @patch("billing.views.verify_transaction")
    def test_stored_hash_is_normalized_to_lowercase(self, mock_verify):
        mock_verify.return_value = Decimal("10.00")
        user = User.objects.create_user(username="pagador8", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user)
        payment = self._create_payment(user)
        self.client.force_login(user)

        self.client.post(
            reverse("billing:crypto_payment_detail", kwargs={"pk": payment.pk}),
            {"tx_hash": " " + "A" * 64 + " "},
        )

        payment.refresh_from_db()
        self.assertEqual(payment.tx_hash, "a" * 64)

    @patch("billing.views.verify_transaction")
    def test_promo_uses_count_never_exceeds_max_uses(self, mock_verify):
        mock_verify.return_value = Decimal("5.00")
        promo = PromoCode.objects.create(code="LIMITADO", discount_percent=50, max_uses=1, uses_count=1)
        user = User.objects.create_user(username="pagador9", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user)
        payment = CryptoPayment.objects.create(
            user=user,
            plan_interval=CustomerProfile.Interval.MONTHLY,
            expected_amount_usdt=Decimal("5.00"),
            promo_code=promo,
            expires_at=timezone.now() + timedelta(minutes=30),
        )
        self.client.force_login(user)

        self.client.post(
            reverse("billing:crypto_payment_detail", kwargs={"pk": payment.pk}),
            {"tx_hash": "b" * 64},
        )

        promo.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(promo.uses_count, 1)
        self.assertEqual(payment.status, CryptoPayment.Status.CONFIRMED)

    @staticmethod
    def _fake_png_bytes() -> bytes:
        buffer = BytesIO()
        PILImage.new("RGB", (10, 10)).save(buffer, format="PNG")
        return buffer.getvalue()
