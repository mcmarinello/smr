"""
Management command: fetch fills and clearinghouseState for a wallet address.

Usage:
    python3 manage.py fetch_wallet <address>
    python3 manage.py fetch_wallet 0x8ff3059f1bf4b0c5f53508f3d0b6f50d992e4263
"""

from django.core.management.base import BaseCommand, CommandError
from wallets.services import fetch_and_persist_wallet


class Command(BaseCommand):
    help = "Fetch fills + clearinghouseState for a wallet address and persist to DB"

    def add_arguments(self, parser):
        parser.add_argument(
            "address",
            type=str,
            help="Hyperliquid wallet address (0x...)",
        )

    def handle(self, *args, **options):
        address = options["address"].strip().lower()
        if not address.startswith("0x") or len(address) != 42:
            raise CommandError(
                f"Invalid address format: {address!r}. Expected 0x-prefixed 42-char hex."
            )

        self.stdout.write(f"Fetching data for {address}...")
        try:
            result = fetch_and_persist_wallet(address)
        except Exception as exc:
            raise CommandError(f"Failed to fetch wallet data: {exc}") from exc

        status = self.style.SUCCESS("CREATED") if result["created"] else "found"
        self.stdout.write(f"Wallet {status}: {result['address']}")
        self.stdout.write(f"  Total fills returned by API : {result['total_fills_returned']}")
        self.stdout.write(f"  New fills persisted          : {result['new_fills_persisted']}")
        self.stdout.write(f"  Open positions               : {result['open_positions']}")
        self.stdout.write(self.style.SUCCESS("Done."))
