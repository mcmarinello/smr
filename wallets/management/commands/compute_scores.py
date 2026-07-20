"""
Management command: compute WalletScore + WalletMetricsWindow for every PRD
§15.2 window (24h / 7d / 30d / 90d / 180d) and persist the results.

By default the command runs over every active wallet in the database.
Pass --address to target a single wallet (useful for trigger/promotion
debugging).

Usage:
    python3 manage.py compute_scores
    python3 manage.py compute_scores --address 0x8ff3059f1bf4b0c5f53508f3d0b6f50d992e4263
    python3 manage.py compute_scores --target-only
    python3 manage.py compute_scores --target-only --address 0x...
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from wallets.models import Wallet, WalletMetricsWindow, WalletScore
from wallets.services import compute_and_persist_scores


def _format_window_report(window_label: str, summary: dict) -> str:
    return (
        f"        {window_label:>5s}  raw={summary['score']:6.2f}  "
        f"delv={summary['score_deleveraged']:6.2f}  "
        f"ldi={summary['leverage_dependency_index']:+.3f}  "
        f"{summary['classification']:<8s}  trades={summary['total_trades']:>5d}"
    )


class Command(BaseCommand):
    help = (
        "Compute WalletScore + WalletMetricsWindow for all PRD §15.2 windows. "
        "Iterates active wallets (or a single wallet via --address)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--address",
            type=str,
            default=None,
            metavar="0x...",
            help="Compute scores for a single wallet address instead of all active wallets",
        )
        parser.add_argument(
            "--target-only",
            action="store_true",
            help="Restrict to wallets flagged as is_target (PRD §15.5 watchlist)",
        )
        parser.add_argument(
            "--include-inactive",
            action="store_true",
            help="Include wallets with is_active=False (skipped by default)",
        )

    def handle(self, *args, **options) -> None:
        address = options.get("address")
        target_only = options.get("target_only", False)
        include_inactive = options.get("include_inactive", False)

        qs = Wallet.objects.all()
        if target_only:
            qs = qs.filter(is_target=True)
        if not include_inactive:
            qs = qs.filter(is_active=True)
        if address:
            address = address.strip().lower()
            if not address.startswith("0x") or len(address) != 42:
                raise CommandError(
                    f"Invalid address format: {address!r}. Expected 0x-prefixed 42-char hex."
                )
            qs = qs.filter(address=address)
            if not qs.exists():
                raise CommandError(f"No wallet found for address {address}")

        wallets = list(qs.order_by("address"))
        total = len(wallets)
        if total == 0:
            self.stdout.write(self.style.WARNING("No wallets matched — nothing to do."))
            return

        self.stdout.write(
            f"Computing scores for {total} wallet(s) "
            f"({len(WalletMetricsWindow.Window.choices)} windows each)..."
        )

        ok = 0
        for index, wallet in enumerate(wallets, start=1):
            prefix = f"[{index:>4d}/{total}] {wallet.address}"
            try:
                summary = compute_and_persist_scores(wallet)
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"{prefix} — FAILED: {exc}"))
                continue

            ok += 1
            self.stdout.write(self.style.SUCCESS(f"{prefix} — OK"))
            for window_label, _ in WalletMetricsWindow.Window.choices:
                self.stdout.write(_format_window_report(window_label, summary[window_label]))

        self.stdout.write(self.style.SUCCESS(f"\nDone — {ok}/{total} wallets scored."))
        self.stdout.write(
            f"Stored {WalletMetricsWindow.objects.count()} metrics rows "
            f"and {WalletScore.objects.count()} score rows total."
        )
