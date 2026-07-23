from django.template import Context, Template
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from alerts.models import Notification
from billing.models import CustomerProfile
from wallets.models import Wallet, WalletScore, Window


class MaskAddressFilterTest(TestCase):
    def _render(self, address: str) -> str:
        template = Template("{% load dashboard_extras %}{{ address|mask_address }}")
        return template.render(Context({"address": address}))

    def test_masks_a_normal_address(self):
        result = self._render("0x" + "b" * 40)
        self.assertEqual(result, "0xbbbb••••bbbb")

    def test_short_string_is_returned_unchanged(self):
        result = self._render("0x1234")
        self.assertEqual(result, "0x1234")

    def test_empty_string_is_returned_unchanged(self):
        result = self._render("")
        self.assertEqual(result, "")


class DashboardGatingTest(TestCase):
    def setUp(self):
        self.wallet = Wallet.objects.create(address="0x" + "a" * 40)
        self.gated_urls = [
            reverse("wallet_profile", kwargs={"address": self.wallet.address}),
            reverse("watchlist"),
            reverse("alerts_history"),
            reverse("settings_page"),
            reverse("whale_copy_status"),
            reverse("whale_copy_api_status"),
        ]

    def test_free_customer_redirected_from_all_gated_views(self):
        user = User.objects.create_user(username="freeuser", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user, status=CustomerProfile.Status.FREE)
        self.client.force_login(user)
        for url in self.gated_urls:
            response = self.client.get(url)
            self.assertRedirects(response, reverse("billing:subscribe_required"), msg_prefix=f"URL {url}: ")

    def test_active_customer_can_access_all_gated_views(self):
        user = User.objects.create_user(username="activeuser", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user, status=CustomerProfile.Status.ACTIVE)
        self.client.force_login(user)
        for url in self.gated_urls:
            response = self.client.get(url)
            self.assertNotEqual(response.status_code, 302, f"URL {url} unexpectedly redirected")

    def test_staff_can_access_all_gated_views(self):
        user = User.objects.create_user(username="staffuser", password="x", role=User.Role.ADMIN)
        self.client.force_login(user)
        for url in self.gated_urls:
            response = self.client.get(url)
            self.assertNotEqual(response.status_code, 302, f"URL {url} unexpectedly redirected")


class DashboardFreemiumMaskingTest(TestCase):
    def setUp(self):
        self.wallet = Wallet.objects.create(address="0x" + "b" * 40)
        WalletScore.objects.create(wallet=self.wallet, window=Window.D7, computed_at=timezone.now())

    def test_free_customer_sees_masked_address_in_discovery_ranking(self):
        user = User.objects.create_user(username="free4", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user, status=CustomerProfile.Status.FREE)
        self.client.force_login(user)

        response = self.client.get(reverse("discovery_ranking"))

        self.assertContains(response, "js-paywall-trigger")
        self.assertContains(response, "•")

    def test_active_customer_sees_real_link_in_discovery_ranking(self):
        user = User.objects.create_user(username="active4", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user, status=CustomerProfile.Status.ACTIVE)
        self.client.force_login(user)

        response = self.client.get(reverse("discovery_ranking"))

        self.assertNotContains(response, "js-paywall-trigger")
        self.assertNotContains(response, "•")

    def test_free_customer_sees_masked_address_in_dashboard_home(self):
        user = User.objects.create_user(username="free5", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user, status=CustomerProfile.Status.FREE)
        Notification.objects.create(user=user, wallet=self.wallet, title="Teste", body="Teste", event_type="test")
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard_home"))

        self.assertContains(response, "js-paywall-trigger")
        self.assertContains(response, "•")

    def test_active_customer_sees_real_link_in_dashboard_home(self):
        user = User.objects.create_user(username="active5", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user, status=CustomerProfile.Status.ACTIVE)
        Notification.objects.create(user=user, wallet=self.wallet, title="Teste", body="Teste", event_type="test")
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard_home"))

        self.assertNotContains(response, "js-paywall-trigger")
        self.assertNotContains(response, "•")
