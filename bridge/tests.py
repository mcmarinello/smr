from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from wallets.models import (
    Position,
    Wallet,
    WalletScore,
    Window,
)

from bridge.models import BridgeAccessLog


@override_settings(TMT_BRIDGE_ENABLED=False)
class SmartMoneySignalDisabledTest(TestCase):
    """The bridge ships off by default — primera camada de segurança."""

    def test_503_when_bridge_disabled(self):
        response = self.client.get(reverse("smart_money_signal"))
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["error"], "Bridge disabled")
        self.assertEqual(
            body["message"], "Set TMT_BRIDGE_ENABLED=True to activate"
        )
        # Even a denied request is audited — the bridge is a contract.
        log = BridgeAccessLog.objects.get()
        self.assertEqual(log.response_code, 503)
        self.assertEqual(log.endpoint, "/api/bridge/v1/smart-money-signal/")
        self.assertEqual(log.data_snapshot["error"], "Bridge disabled")


@override_settings(TMT_BRIDGE_ENABLED=True)
class SmartMoneySignalEnabledTest(TestCase):
    """Behavioural contract while the bridge flag is explicitly on."""

    @classmethod
    def setUpTestData(cls):
        cls.now = timezone.now()
        # Three wallets with three distinct scores straddling the default
        # min_score boundary (70) and a second window (30d) for filter tests.
        cls.wallet_elite = Wallet.objects.create(
            address="0x" + "11" * 20, is_target=True, is_active=True
        )
        cls.wallet_bom = Wallet.objects.create(
            address="0x" + "22" * 20, is_target=True, is_active=True
        )
        cls.wallet_fraco = Wallet.objects.create(
            address="0x" + "33" * 20, is_target=True, is_active=True
        )
        WalletScore.objects.create(
            wallet=cls.wallet_elite,
            window=Window.D7.value,
            computed_at=cls.now,
            score_raw=Decimal("88.500"),
            classification=WalletScore.Classification.ELITE.value,
        )
        WalletScore.objects.create(
            wallet=cls.wallet_bom,
            window=Window.D7.value,
            computed_at=cls.now,
            score_raw=Decimal("72.000"),
            classification=WalletScore.Classification.BOM.value,
        )
        WalletScore.objects.create(
            wallet=cls.wallet_fraco,
            window=Window.D7.value,
            computed_at=cls.now,
            score_raw=Decimal("45.000"),
            classification=WalletScore.Classification.FRACO.value,
        )
        # Different-window score must never leak into 7d responses.
        WalletScore.objects.create(
            wallet=cls.wallet_fraco,
            window=Window.D30.value,
            computed_at=cls.now,
            score_raw=Decimal("95.000"),
            classification=WalletScore.Classification.ELITE.value,
        )
        # An open position on the elite wallet — surfaces in the
        # last_position_summary payload.
        Position.objects.create(
            wallet=cls.wallet_elite,
            asset="BTC",
            side=Position.Side.LONG,
            size=Decimal("0.5"),
            entry_price=Decimal("60000"),
            leverage=Decimal("2.0"),
            unrealized_pnl=Decimal("1500"),
            status=Position.Status.OPEN,
            opened_at=cls.now,
        )

    def test_200_with_valid_data_when_enabled(self):
        response = self.client.get(reverse("smart_money_signal"))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["window"], "7d")
        self.assertEqual(body["count"], 2)  # elite + bom only (>=70)
        # Ranked by score_raw descending — elite first.
        self.assertEqual(body["results"][0]["address"], self.wallet_elite.address)
        self.assertEqual(body["results"][0]["score"], 88.5)
        self.assertEqual(body["results"][0]["classification"], "elite")
        self.assertEqual(body["results"][1]["address"], self.wallet_bom.address)
        self.assertEqual(body["results"][1]["classification"], "bom")
        # Open-position snapshot included.
        summary = body["results"][0]["last_position_summary"]
        self.assertEqual(summary["open_count"], 1)
        self.assertEqual(summary["positions"][0]["asset"], "BTC")
        # Audit row created with the full snapshot.
        log = BridgeAccessLog.objects.get(response_code=200)
        self.assertEqual(log.data_snapshot["window"], "7d")
        self.assertEqual(log.data_snapshot["count"], 2)

    def test_query_param_filtering_min_score(self):
        # Raise the threshold above the bom wallet — only elite survives.
        response = self.client.get(
            reverse("smart_money_signal"), {"min_score": "80"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"][0]["address"], self.wallet_elite.address)

    def test_query_param_filtering_window(self):
        # The 30d score of the weak wallet is elite-tier — switching the
        # window changes the universe returned.
        response = self.client.get(
            reverse("smart_money_signal"), {"window": "30d"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["window"], "30d")
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"][0]["address"], self.wallet_fraco.address)
        self.assertEqual(float(body["results"][0]["score"]), 95.0)

    def test_query_param_filtering_limit(self):
        # limit=1 truncates the leaderboard even when more qualify.
        response = self.client.get(
            reverse("smart_money_signal"), {"limit": "1"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["limit"], 1)
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"][0]["address"], self.wallet_elite.address)

    def test_invalid_window_returns_400(self):
        response = self.client.get(
            reverse("smart_money_signal"), {"window": "3d"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_window")
        self.assertTrue(
            BridgeAccessLog.objects.filter(response_code=400).exists()
        )

    def test_invalid_min_score_returns_400(self):
        response = self.client.get(
            reverse("smart_money_signal"), {"min_score": "not-a-number"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_min_score")

    def test_invalid_limit_returns_400(self):
        response = self.client.get(
            reverse("smart_money_signal"), {"limit": "0"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_limit")