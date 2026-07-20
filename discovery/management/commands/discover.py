"""
Management command: run the Discovery Engine once, synchronously.

Useful for manual testing and initial seeding without a running Celery broker.
Backfills are executed in-process (not queued) so the command is self-contained.

Usage:
    python3 manage.py discover
    python3 manage.py discover --no-stream
    python3 manage.py discover --stream-duration 60
    python3 manage.py discover --leaderboard-only
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from discovery.services import run_leaderboard_scan, run_trade_stream_scan
from wallets.services import fetch_and_persist_wallet


class Command(BaseCommand):
    help = "Run discovery engine once (leaderboard + optional trade stream) without Celery"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--stream-duration",
            type=int,
            default=30,
            metavar="SECS",
            help="Seconds to listen to the trade stream (default: 30)",
        )
        parser.add_argument(
            "--no-stream",
            action="store_true",
            help="Skip the trade stream; only run the leaderboard fetch",
        )
        parser.add_argument(
            "--no-backfill",
            action="store_true",
            help="Skip backfilling fills/positions for newly discovered wallets",
        )

    def handle(self, *args, **options) -> None:
        no_backfill: bool = options["no_backfill"]

        # ------------------------------------------------------------------ #
        # Step 1 — Leaderboard
        # ------------------------------------------------------------------ #
        self.stdout.write("[ 1/2 ] Running leaderboard scan…")
        try:
            lb = run_leaderboard_scan()
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Leaderboard scan failed: {exc}"))
            return

        self.stdout.write(
            f"        {lb['total']} addresses seen, "
            f"{self.style.SUCCESS(str(lb['new']) + ' new wallets')}"
        )

        if lb["new_addresses"] and not no_backfill:
            self.stdout.write(f"        Backfilling {lb['new']} new wallet(s)…")
            self._run_backfills(lb["new_addresses"])

        # ------------------------------------------------------------------ #
        # Step 2 — Trade stream
        # ------------------------------------------------------------------ #
        if not options["no_stream"]:
            duration = options["stream_duration"]
            self.stdout.write(f"[ 2/2 ] Running trade stream for {duration}s…")
            try:
                st = run_trade_stream_scan(duration)
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"Trade stream scan failed: {exc}"))
            else:
                self.stdout.write(
                    f"        {st['total']} unique addresses seen "
                    f"across {st['coins_subscribed']} coins, "
                    f"{self.style.SUCCESS(str(st['new']) + ' new wallets')}"
                )
                if st["new_addresses"] and not no_backfill:
                    self.stdout.write(f"        Backfilling {st['new']} new wallet(s)…")
                    self._run_backfills(st["new_addresses"])
        else:
            self.stdout.write("[ 2/2 ] Trade stream skipped (--no-stream).")

        # ------------------------------------------------------------------ #
        # Summary
        # ------------------------------------------------------------------ #
        from discovery.services import get_status_summary
        summary = get_status_summary()
        self.stdout.write("\n--- Discovery Status ---")
        for source, info in summary.items():
            self.stdout.write(
                f"  {source:15s}  total={info['discovered_count']:>6d}  "
                f"last_scan={info['last_scan_at'] or 'never'}"
            )
        self.stdout.write(self.style.SUCCESS("\nDiscovery run complete."))

    def _run_backfills(self, addresses: list[str]) -> None:
        for i, address in enumerate(addresses, start=1):
            try:
                result = fetch_and_persist_wallet(address)
                self.stdout.write(
                    f"          [{i}/{len(addresses)}] {address} — "
                    f"{result['new_fills_persisted']} fills, "
                    f"{result['open_positions']} positions"
                )
            except Exception as exc:
                self.stderr.write(
                    self.style.WARNING(f"          [{i}/{len(addresses)}] {address} — backfill error: {exc}")
                )
