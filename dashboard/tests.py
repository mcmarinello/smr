from django.template import Context, Template
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from billing.models import CustomerProfile
from wallets.models import Wallet


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
