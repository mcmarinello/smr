# TRC-20 Crypto Billing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a customer pay for a monthly or annual subscription in USDT on the Tron network (TRC-20), confirming via a transaction hash (typed or extracted from a screenshot via OCR), verified directly against the blockchain, activating `CustomerProfile` in real time.

**Architecture:** All new code lives in the existing `billing` app, alongside `CustomerProfile`/`ExchangeCredential`/`Favorite`. Two new models (`PromoCode`, `CryptoPayment`) track discount codes and payment intents. Two new standalone modules (`billing/tron.py`, `billing/ocr.py`) handle blockchain verification and hash extraction, each independently testable via mocking. Two new views drive the customer-facing flow (choose plan → pay → confirm), reusing `CustomerProfile` activation logic already established by the multi-tenant foundation plan.

**Tech Stack:** Django 5.2.16, `httpx` + `tenacity` (already in requirements, same pattern as `hyperliquid_client`) for the TronGrid HTTP client, `pytesseract` + `Pillow` (new) for OCR, `manage.py test` (`TestCase`, `unittest.mock.patch`).

## Global Constraints

- Código em inglês, UI em pt-BR — Python identifiers/comments in English, all template copy and user-facing error messages in Portuguese.
- Self-custody only: `TRC20_WALLET_ADDRESS` is a public address setting. No private key is ever stored or required by this codebase — receiving USDT-TRC20 needs no signature.
- No amount-uniqueness trick. Plan prices are the plain configured values (`TRC20_MONTHLY_PRICE_USDT` / `TRC20_ANNUAL_PRICE_USDT`, possibly reduced by a promo code's discount) — payment identification is entirely via `tx_hash`, which must be `unique=True` on `CryptoPayment`.
- Verification is synchronous, on-demand, triggered by the customer submitting a hash (typed or OCR'd) — no background poller scanning the wallet.
- OCR failure must show a friendly Portuguese error and let the customer type the hash manually in the same form — never a hard dead end, never a manual-review queue.
- Received amount ≥ expected amount is accepted (no refund logic for overpayment); received amount < expected is rejected.
- Follow existing repo conventions: `BaseModel` from `wallets.models` for `created_at`/`updated_at`; `TestCase` + `unittest.mock.patch` (no new test framework); one `tests.py` per app — everything in this plan's tests appends to the existing `billing/tests.py`; dark-theme template style matching `templates/registration/*.html` already in the repo (bg `#0f1117`, box `#1a1d27`, accent `#4f9eff`).

---

### Task 1: Bootstrap OCR/Tron dependencies

**Files:**
- Modify: `requirements.txt`
- Modify: `Dockerfile`
- Modify: `.env.example`

**Interfaces:**
- Produces: `pytesseract` and `Pillow` importable in the `web` image; `tesseract-ocr` binary available on `PATH` inside the container (used by `pytesseract` at runtime — not required for this plan's tests, which mock all OCR calls, but required for the feature to function for real).

- [ ] **Step 1: Add the Python dependencies**

In `requirements.txt`, add under "Django utilities" (after `cryptography==44.0.0`):
```
django-extensions==4.1
cryptography==44.0.0
pytesseract==0.3.13
Pillow==11.1.0
```

- [ ] **Step 2: Add the Tesseract binary to the image**

In `Dockerfile`, add `tesseract-ocr` to the existing `apt-get install` line:
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 3: Document the new settings in `.env.example`**

In `.env.example`, add a new section after "Ponte com o TMT":
```
# Billing via cripto (TRC-20 USDT na rede Tron)
TRC20_WALLET_ADDRESS=
TRC20_USDT_CONTRACT_ADDRESS=TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t
TRONGRID_API_URL=https://api.trongrid.io
TRONGRID_API_KEY=
TRC20_MONTHLY_PRICE_USDT=10.00
TRC20_ANNUAL_PRICE_USDT=100.00
TRC20_PAYMENT_EXPIRY_MINUTES=30
```

- [ ] **Step 4: Rebuild the image and verify**

Run: `docker compose build web`
Expected: build succeeds, `Successfully installed ... pytesseract-0.3.13 Pillow-11.1.0` in the output.

Run: `docker compose run --rm web python -c "import pytesseract, PIL; print('ok')"`
Expected: `ok`

Run: `docker compose run --rm web python manage.py check`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 5: Commit**

```bash
git add requirements.txt Dockerfile .env.example
git commit -m "feat(billing): add OCR/Tron dependencies for crypto billing"
```

---

### Task 2: `PromoCode` model

**Files:**
- Modify: `billing/models.py`
- Modify: `billing/admin.py`
- Create: `billing/migrations/0004_promocode.py` (generated)
- Modify: `billing/tests.py`

**Interfaces:**
- Produces: `PromoCode` with `code` (unique), `discount_percent` (0-100), `max_uses` (nullable = unlimited), `uses_count`, `valid_until` (nullable), `is_active`, and `is_valid() -> bool`. Task 7 (`SubscribeChoosePlanView`) depends on `PromoCode.objects.filter(code=...)` and `is_valid()`.

- [ ] **Step 1: Write the failing tests**

`billing/tests.py` — add to the imports at the top:
```python
from billing.models import CustomerProfile, ExchangeCredential, Favorite, PromoCode
```
(replacing the existing `from billing.models import CustomerProfile, ExchangeCredential, Favorite` line)

Add the test class:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm web python manage.py test billing.tests.PromoCodeTest -v 2`
Expected: `FAIL` / `ImportError: cannot import name 'PromoCode' from 'billing.models'`

- [ ] **Step 3: Write the model**

`billing/models.py` — add `from django.core.validators import MaxValueValidator` and `from django.utils import timezone` to the top imports (final import block):
```python
from __future__ import annotations

from django.core.validators import MaxValueValidator
from django.db import models
from django.utils import timezone

from accounts.models import User
from billing.crypto import decrypt_secret, encrypt_secret
from wallets.models import BaseModel, Wallet
```

Add at the end of the file:
```python
class PromoCode(BaseModel):
    code = models.CharField(max_length=32, unique=True)
    discount_percent = models.PositiveIntegerField(validators=[MaxValueValidator(100)])
    max_uses = models.PositiveIntegerField(null=True, blank=True)  # None = unlimited
    uses_count = models.PositiveIntegerField(default=0)
    valid_until = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def is_valid(self) -> bool:
        if not self.is_active:
            return False
        if self.valid_until and timezone.now() > self.valid_until:
            return False
        if self.max_uses is not None and self.uses_count >= self.max_uses:
            return False
        return True

    def __str__(self) -> str:
        return f"{self.code} (-{self.discount_percent}%)"
```

- [ ] **Step 4: Register in admin**

`billing/admin.py` — update the import line and add:
```python
from billing.models import CustomerProfile, ExchangeCredential, Favorite, PromoCode


@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "discount_percent", "uses_count", "max_uses", "valid_until", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code",)
```

- [ ] **Step 5: Generate and apply the migration**

Run: `docker compose run --rm web python manage.py makemigrations billing`
Expected: `Migrations for 'billing': billing/migrations/0004_promocode.py - Create model PromoCode`

Run: `docker compose run --rm web python manage.py migrate billing`
Expected: `Applying billing.0004_promocode... OK`

- [ ] **Step 6: Run tests to verify they pass**

Run: `docker compose run --rm web python manage.py test billing -v 2`
Expected: `OK` (33 tests)

- [ ] **Step 7: Commit**

```bash
git add billing/models.py billing/admin.py billing/migrations/0004_promocode.py billing/tests.py
git commit -m "feat(billing): add PromoCode model"
```

---

### Task 3: `CryptoPayment` model

**Files:**
- Modify: `billing/models.py`
- Modify: `billing/admin.py`
- Create: `billing/migrations/0005_cryptopayment.py` (generated)
- Modify: `billing/tests.py`

**Interfaces:**
- Consumes: `PromoCode`, `CustomerProfile.Interval`
- Produces: `CryptoPayment` with `user` (FK), `plan_interval`, `expected_amount_usdt` (Decimal), `promo_code` (nullable FK), `status` (`CryptoPayment.Status.PENDING|CONFIRMED|EXPIRED`), `tx_hash` (unique, nullable), `expires_at`, `confirmed_at` (nullable). Tasks 7, 8, 9 depend on this exact shape.

- [ ] **Step 1: Write the failing tests**

`billing/tests.py` — add to imports:
```python
from decimal import Decimal
```
(new top-level import, before `from django.core import mail`)

Update the models import line to:
```python
from billing.models import CryptoPayment, CustomerProfile, ExchangeCredential, Favorite, PromoCode
```

Add the test class:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm web python manage.py test billing.tests.CryptoPaymentTest -v 2`
Expected: `FAIL` / `ImportError: cannot import name 'CryptoPayment' from 'billing.models'`

- [ ] **Step 3: Write the model**

`billing/models.py` — add at the end:
```python
class CryptoPayment(BaseModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        EXPIRED = "expired", "Expired"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="crypto_payments")
    plan_interval = models.CharField(max_length=20, choices=CustomerProfile.Interval.choices)
    expected_amount_usdt = models.DecimalField(max_digits=12, decimal_places=6)
    promo_code = models.ForeignKey(PromoCode, null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    tx_hash = models.CharField(max_length=64, unique=True, null=True, blank=True)
    expires_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.user.username} — {self.expected_amount_usdt} USDT ({self.status})"
```

- [ ] **Step 4: Register in admin**

`billing/admin.py` — add:
```python
from billing.models import CryptoPayment, CustomerProfile, ExchangeCredential, Favorite, PromoCode


@admin.register(CryptoPayment)
class CryptoPaymentAdmin(admin.ModelAdmin):
    list_display = ("user", "plan_interval", "expected_amount_usdt", "status", "expires_at", "confirmed_at")
    list_filter = ("status", "plan_interval")
    search_fields = ("user__username", "tx_hash")
    raw_id_fields = ("user", "promo_code")
```

- [ ] **Step 5: Generate and apply the migration**

Run: `docker compose run --rm web python manage.py makemigrations billing`
Expected: `Migrations for 'billing': billing/migrations/0005_cryptopayment.py - Create model CryptoPayment`

Run: `docker compose run --rm web python manage.py migrate billing`
Expected: `Applying billing.0005_cryptopayment... OK`

- [ ] **Step 6: Run tests to verify they pass**

Run: `docker compose run --rm web python manage.py test billing -v 2`
Expected: `OK` (36 tests)

- [ ] **Step 7: Commit**

```bash
git add billing/models.py billing/admin.py billing/migrations/0005_cryptopayment.py billing/tests.py
git commit -m "feat(billing): add CryptoPayment model"
```

---

### Task 4: TRC-20 settings

**Files:**
- Modify: `smr/settings.py`

**Interfaces:**
- Produces: `settings.TRC20_WALLET_ADDRESS`, `settings.TRC20_USDT_CONTRACT_ADDRESS`, `settings.TRONGRID_API_URL`, `settings.TRONGRID_API_KEY`, `settings.TRC20_MONTHLY_PRICE_USDT`, `settings.TRC20_ANNUAL_PRICE_USDT`, `settings.TRC20_PAYMENT_EXPIRY_MINUTES`. Tasks 5, 7, 8, 9 all read these.

- [ ] **Step 1: Add the settings**

In `smr/settings.py`, immediately after the `EMAIL_VERIFICATION_REQUIRED` line, add:
```python
# TRC-20 crypto billing (USDT on Tron) —
# docs/specs/2026-07-23-trc20-crypto-billing-design.md. Self-custody only:
# TRC20_WALLET_ADDRESS is a public address, never a private key.
TRC20_WALLET_ADDRESS = config("TRC20_WALLET_ADDRESS", default="")
TRC20_USDT_CONTRACT_ADDRESS = config(
    "TRC20_USDT_CONTRACT_ADDRESS", default="TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
)
TRONGRID_API_URL = config("TRONGRID_API_URL", default="https://api.trongrid.io")
TRONGRID_API_KEY = config("TRONGRID_API_KEY", default="")
TRC20_MONTHLY_PRICE_USDT = config("TRC20_MONTHLY_PRICE_USDT", default=10.00, cast=float)
TRC20_ANNUAL_PRICE_USDT = config("TRC20_ANNUAL_PRICE_USDT", default=100.00, cast=float)
TRC20_PAYMENT_EXPIRY_MINUTES = config("TRC20_PAYMENT_EXPIRY_MINUTES", default=30, cast=int)
```

- [ ] **Step 2: Verify**

Run: `docker compose run --rm web python manage.py check`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 3: Commit**

```bash
git add smr/settings.py
git commit -m "feat(billing): add TRC-20 settings"
```

---

### Task 5: Tron verification client (`billing/tron.py`)

**Files:**
- Create: `billing/tron.py`
- Modify: `billing/tests.py`

**Interfaces:**
- Consumes: `settings.TRONGRID_API_URL`, `settings.TRONGRID_API_KEY`, `settings.TRC20_USDT_CONTRACT_ADDRESS`, `settings.TRC20_WALLET_ADDRESS`
- Produces: `TronVerificationError` (exception, message is a ready-to-display pt-BR string), `verify_transaction(tx_hash: str, expected_amount: Decimal) -> Decimal` (returns actual amount received, raises `TronVerificationError` on any failure). Task 8 (`CryptoPaymentDetailView`) depends on this exact signature.

- [ ] **Step 1: Write the failing tests**

`billing/tests.py` — add to imports:
```python
from unittest.mock import patch
```
(new top-level import, alongside the other stdlib imports)

Add the test class:
```python
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
```

Also add, alongside the other top-of-file imports:
```python
from django.conf import settings
```
(check first — `billing/tests.py` may not import `settings` yet; add only if missing)

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm web python manage.py test billing.tests.VerifyTransactionTest -v 2`
Expected: `FAIL` / `ModuleNotFoundError: No module named 'billing.tron'`

- [ ] **Step 3: Write the client**

`billing/tron.py`:
```python
from __future__ import annotations

from decimal import Decimal

import httpx
from django.conf import settings
from tenacity import retry, stop_after_attempt, wait_exponential

USDT_DECIMALS = 6


class TronVerificationError(Exception):
    """Message is a ready-to-display pt-BR string explaining the failure."""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def _fetch_transaction_events(tx_hash: str) -> list[dict]:
    headers = {"TRON-PRO-API-KEY": settings.TRONGRID_API_KEY} if settings.TRONGRID_API_KEY else {}
    response = httpx.get(
        f"{settings.TRONGRID_API_URL}/v1/transactions/{tx_hash}/events",
        headers=headers,
        timeout=10,
    )
    response.raise_for_status()
    return response.json().get("data", [])


def verify_transaction(tx_hash: str, expected_amount: Decimal) -> Decimal:
    """Returns the actual amount received (USDT), or raises TronVerificationError."""
    events = _fetch_transaction_events(tx_hash)

    for event in events:
        if event.get("event_name") != "Transfer":
            continue
        if event.get("contract_address") != settings.TRC20_USDT_CONTRACT_ADDRESS:
            continue
        result = event.get("result", {})
        if result.get("to") != settings.TRC20_WALLET_ADDRESS:
            continue

        amount = Decimal(result["value"]) / Decimal(10**USDT_DECIMALS)
        if amount < expected_amount:
            raise TronVerificationError(
                f"Valor recebido ({amount} USDT) é menor que o esperado ({expected_amount} USDT)."
            )
        return amount

    raise TronVerificationError(
        "Transação não encontrada ou ainda não confirmada. Tente novamente em alguns segundos."
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm web python manage.py test billing.tests.VerifyTransactionTest -v 2`
Expected: `OK` (6 tests)

- [ ] **Step 5: Run the full billing suite**

Run: `docker compose run --rm web python manage.py test billing -v 2`
Expected: `OK` (42 tests)

- [ ] **Step 6: Commit**

```bash
git add billing/tron.py billing/tests.py
git commit -m "feat(billing): add Tron transaction verification client"
```

**Note for whoever validates this against production:** this endpoint shape
(`/v1/transactions/{id}/events`, `result.to`/`result.from` as base58
addresses, `result.value` as a raw integer string) is TronGrid's documented
decoded-event format, but it is only exercised here against mocked
responses — verify one real call against TronGrid manually (e.g. with
`curl`) before enabling this in production, since automated tests cannot
hit the live network.

---

### Task 6: Hash extraction from screenshot (`billing/ocr.py`)

**Files:**
- Create: `billing/ocr.py`
- Modify: `billing/tests.py`

**Interfaces:**
- Produces: `extract_tx_hash(image_file) -> str | None`. Task 8 (`CryptoPaymentDetailView`) depends on this exact signature.

- [ ] **Step 1: Write the failing tests**

`billing/tests.py` — add to imports:
```python
from io import BytesIO

from PIL import Image as PILImage
```

Add the test class:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm web python manage.py test billing.tests.ExtractTxHashTest -v 2`
Expected: `FAIL` / `ModuleNotFoundError: No module named 'billing.ocr'`

- [ ] **Step 3: Write the module**

`billing/ocr.py`:
```python
from __future__ import annotations

import re

import pytesseract
from PIL import Image

_HASH_PATTERN = re.compile(r"\b[a-fA-F0-9]{64}\b")


def extract_tx_hash(image_file) -> str | None:
    text = pytesseract.image_to_string(Image.open(image_file))
    match = _HASH_PATTERN.search(text)
    return match.group(0) if match else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm web python manage.py test billing.tests.ExtractTxHashTest -v 2`
Expected: `OK` (2 tests)

- [ ] **Step 5: Run the full billing suite**

Run: `docker compose run --rm web python manage.py test billing -v 2`
Expected: `OK` (44 tests)

- [ ] **Step 6: Commit**

```bash
git add billing/ocr.py billing/tests.py
git commit -m "feat(billing): add OCR-based transaction hash extraction"
```

---

### Task 7: Choose-plan screen

**Files:**
- Modify: `billing/forms.py`
- Modify: `billing/views.py`
- Modify: `billing/urls.py`
- Create: `templates/registration/subscribe_choose_plan.html`
- Modify: `billing/tests.py`

**Interfaces:**
- Consumes: `billing.models.PromoCode`, `billing.models.CryptoPayment`, `settings.TRC20_MONTHLY_PRICE_USDT`, `settings.TRC20_ANNUAL_PRICE_USDT`, `settings.TRC20_PAYMENT_EXPIRY_MINUTES`
- Produces: URL `billing:subscribe_choose_plan`. Task 8 depends on the `CryptoPayment` rows this view creates and on the URL name `billing:crypto_payment_detail` it redirects to (which Task 8 defines).

- [ ] **Step 1: Write the failing tests**

`billing/tests.py` — add to imports:
```python
from django.conf import settings
```
(if not already added in Task 5)

Add the test class:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm web python manage.py test billing.tests.SubscribeChoosePlanViewTest -v 2`
Expected: `FAIL` / `NoReverseMatch: 'subscribe_choose_plan' is not a registered namespace member`

- [ ] **Step 3: Write the form**

`billing/forms.py` — add:
```python
from billing.models import CustomerProfile


class SubscribeChoosePlanForm(forms.Form):
    plan_interval = forms.ChoiceField(choices=CustomerProfile.Interval.choices)
    promo_code = forms.CharField(max_length=32, required=False)
```

- [ ] **Step 4: Write the view**

`billing/views.py` — add imports and the view:
```python
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse
from django.utils import timezone

from billing.forms import SubscribeChoosePlanForm
from billing.models import CryptoPayment, PromoCode


class SubscribeChoosePlanView(LoginRequiredMixin, FormView):
    template_name = "registration/subscribe_choose_plan.html"
    form_class = SubscribeChoosePlanForm

    def form_valid(self, form):
        plan_interval = form.cleaned_data["plan_interval"]
        promo_input = form.cleaned_data["promo_code"]

        if plan_interval == CustomerProfile.Interval.MONTHLY:
            amount = Decimal(str(settings.TRC20_MONTHLY_PRICE_USDT))
        else:
            amount = Decimal(str(settings.TRC20_ANNUAL_PRICE_USDT))

        promo = None
        if promo_input:
            promo = PromoCode.objects.filter(code=promo_input).first()
            if promo is None or not promo.is_valid():
                form.add_error("promo_code", "Código promocional inválido ou expirado.")
                return self.form_invalid(form)
            amount = amount * (Decimal(100 - promo.discount_percent) / Decimal(100))

        payment = CryptoPayment.objects.create(
            user=self.request.user,
            plan_interval=plan_interval,
            expected_amount_usdt=amount,
            promo_code=promo,
            expires_at=timezone.now() + timedelta(minutes=settings.TRC20_PAYMENT_EXPIRY_MINUTES),
        )
        self.success_url = reverse("billing:crypto_payment_detail", kwargs={"pk": payment.pk})
        return super().form_valid(form)
```

- [ ] **Step 5: Wire the URL**

`billing/urls.py` — add:
```python
    path("assinar/", views.SubscribeChoosePlanView.as_view(), name="subscribe_choose_plan"),
```

- [ ] **Step 6: Write the template**

`templates/registration/subscribe_choose_plan.html`:
```html
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Assinar — SMR</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f1117; color: #e0e0e0; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        .box { background: #1a1d27; padding: 40px; border-radius: 12px; width: 100%; max-width: 400px; }
        h1 { text-align: center; color: #4f9eff; }
        label { display: block; margin: 16px 0 8px; font-size: 14px; color: #9ca3af; }
        select, input { width: 100%; padding: 12px; border: 1px solid #374151; border-radius: 8px; background: #0f1117; color: #e0e0e0; box-sizing: border-box; }
        button { width: 100%; margin-top: 20px; padding: 12px; background: #4f9eff; color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; }
        .error { color: #ef4444; font-size: 13px; margin-top: 8px; }
    </style>
</head>
<body>
    <div class="box">
        <h1>Assinar o SMR</h1>
        <form method="post">
            {% csrf_token %}
            <label for="id_plan_interval">Plano</label>
            {{ form.plan_interval }}
            <label for="id_promo_code">Código promocional (opcional)</label>
            {{ form.promo_code }}
            {% for error in form.promo_code.errors %}<p class="error">{{ error }}</p>{% endfor %}
            <button type="submit">Continuar</button>
        </form>
    </div>
</body>
</html>
```

- [ ] **Step 7: Run tests to verify they pass**

This task's tests reference `billing:crypto_payment_detail`, defined in Task 8 — running now will fail on that URL name specifically for the two success-path tests. That's expected; confirm only that the failure is `NoReverseMatch: 'crypto_payment_detail'` (proving everything else in this task works), not any other error:

Run: `docker compose run --rm web python manage.py test billing.tests.SubscribeChoosePlanViewTest -v 2`
Expected: `test_rejects_invalid_promo_code` and `test_requires_login` PASS; `test_creates_pending_payment_without_promo` and `test_applies_valid_promo_code_discount` FAIL with `NoReverseMatch: Reverse for 'crypto_payment_detail' not found`.

- [ ] **Step 8: Commit**

```bash
git add billing/forms.py billing/views.py billing/urls.py billing/tests.py templates/registration/subscribe_choose_plan.html
git commit -m "feat(billing): add choose-plan screen for crypto billing"
```

---

### Task 8: Payment confirmation screen

**Files:**
- Modify: `billing/forms.py`
- Modify: `billing/views.py`
- Modify: `billing/urls.py`
- Modify: `templates/registration/subscribe_required.html`
- Create: `templates/registration/crypto_payment_detail.html`
- Modify: `billing/tests.py`

**Interfaces:**
- Consumes: `billing.tron.verify_transaction`, `billing.tron.TronVerificationError`, `billing.ocr.extract_tx_hash`, `billing.models.CryptoPayment`, `billing.models.PromoCode`
- Produces: URL `billing:crypto_payment_detail`. Completes Task 7's `SubscribeChoosePlanViewTest` (the two tests left failing there will now pass).

- [ ] **Step 1: Write the failing tests**

`billing/tests.py` — add to imports:
```python
from django.core.files.uploadedfile import SimpleUploadedFile

from billing.tron import TronVerificationError
```

Add the test class:
```python
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

    @staticmethod
    def _fake_png_bytes() -> bytes:
        buffer = BytesIO()
        PILImage.new("RGB", (10, 10)).save(buffer, format="PNG")
        return buffer.getvalue()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm web python manage.py test billing.tests.CryptoPaymentDetailViewTest -v 2`
Expected: `FAIL` / `NoReverseMatch: Reverse for 'crypto_payment_detail' not found`

- [ ] **Step 3: Write the form**

`billing/forms.py` — add:
```python
class CryptoPaymentVerifyForm(forms.Form):
    tx_hash = forms.CharField(max_length=64, required=False)
    screenshot = forms.ImageField(required=False)

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("tx_hash") and not cleaned_data.get("screenshot"):
            raise forms.ValidationError("Informe o hash da transação ou envie um print.")
        return cleaned_data
```

- [ ] **Step 4: Write the view**

`billing/views.py` already has `from django.db import transaction` and `from django.shortcuts import get_object_or_404` (from earlier tasks) — do not add them again. Add only these new imports:
```python
from django.db.models import F

from billing.forms import CryptoPaymentVerifyForm
from billing.ocr import extract_tx_hash
from billing.tron import TronVerificationError, verify_transaction
```

Then add the view:
```python
class CryptoPaymentDetailView(LoginRequiredMixin, FormView):
    template_name = "registration/crypto_payment_detail.html"
    form_class = CryptoPaymentVerifyForm

    def get_payment(self) -> CryptoPayment:
        return get_object_or_404(CryptoPayment, pk=self.kwargs["pk"], user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["payment"] = self.get_payment()
        context["wallet_address"] = settings.TRC20_WALLET_ADDRESS
        return context

    def form_valid(self, form):
        payment = self.get_payment()
        if payment.status != CryptoPayment.Status.PENDING:
            form.add_error(None, "Essa cobrança não está mais pendente.")
            return self.form_invalid(form)

        tx_hash = form.cleaned_data.get("tx_hash")
        screenshot = form.cleaned_data.get("screenshot")

        if not tx_hash and screenshot:
            tx_hash = extract_tx_hash(screenshot)
            if not tx_hash:
                form.add_error(
                    None,
                    "Não conseguimos ler o hash dessa imagem automaticamente. "
                    "Desculpe pelo inconveniente — cole o hash da transação manualmente abaixo.",
                )
                return self.form_invalid(form)

        if CryptoPayment.objects.filter(tx_hash=tx_hash).exclude(pk=payment.pk).exists():
            form.add_error(None, "Essa transação já foi usada em outra cobrança.")
            return self.form_invalid(form)

        try:
            verify_transaction(tx_hash, payment.expected_amount_usdt)
        except TronVerificationError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        with transaction.atomic():
            payment.tx_hash = tx_hash
            payment.status = CryptoPayment.Status.CONFIRMED
            payment.confirmed_at = timezone.now()
            payment.save(update_fields=["tx_hash", "status", "confirmed_at"])

            days = 30 if payment.plan_interval == CustomerProfile.Interval.MONTHLY else 365
            profile, _ = CustomerProfile.objects.get_or_create(user=self.request.user)
            profile.status = CustomerProfile.Status.ACTIVE
            profile.plan_interval = payment.plan_interval
            profile.current_period_end = timezone.now() + timedelta(days=days)
            profile.save(update_fields=["status", "plan_interval", "current_period_end"])

            if payment.promo_code_id:
                PromoCode.objects.filter(pk=payment.promo_code_id).update(uses_count=F("uses_count") + 1)

        self.success_url = reverse("dashboard_home")
        return super().form_valid(form)
```

- [ ] **Step 5: Wire the URL**

`billing/urls.py` — add:
```python
    path("assinar/pagamento/<int:pk>/", views.CryptoPaymentDetailView.as_view(), name="crypto_payment_detail"),
```

- [ ] **Step 6: Write the template**

`templates/registration/crypto_payment_detail.html`:
```html
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Pagamento via cripto — SMR</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f1117; color: #e0e0e0; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        .box { background: #1a1d27; padding: 40px; border-radius: 12px; width: 100%; max-width: 480px; }
        h1 { text-align: center; color: #4f9eff; }
        .amount { text-align: center; font-size: 28px; font-weight: 700; margin: 16px 0; }
        .address { background: #0f1117; border: 1px solid #374151; border-radius: 8px; padding: 12px; word-break: break-all; font-family: monospace; text-align: center; margin-bottom: 24px; }
        label { display: block; margin: 16px 0 8px; font-size: 14px; color: #9ca3af; }
        input { width: 100%; padding: 12px; border: 1px solid #374151; border-radius: 8px; background: #0f1117; color: #e0e0e0; box-sizing: border-box; }
        button { width: 100%; margin-top: 20px; padding: 12px; background: #4f9eff; color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; }
        .error { color: #ef4444; font-size: 13px; margin-top: 8px; }
    </style>
</head>
<body>
    <div class="box">
        <h1>Pague em USDT (rede Tron/TRC-20)</h1>
        <p class="amount">{{ payment.expected_amount_usdt }} USDT</p>
        <p>Envie exatamente esse valor pra este endereço:</p>
        <div class="address">{{ wallet_address }}</div>
        <form method="post" enctype="multipart/form-data">
            {% csrf_token %}
            {% for error in form.non_field_errors %}<p class="error">{{ error }}</p>{% endfor %}
            <label for="id_tx_hash">Hash da transação</label>
            {{ form.tx_hash }}
            <label for="id_screenshot">Ou envie um print da transação</label>
            {{ form.screenshot }}
            <button type="submit">Confirmar pagamento</button>
        </form>
    </div>
</body>
</html>
```

- [ ] **Step 7: Link the flow from the existing paywall page**

`templates/registration/subscribe_required.html` — replace:
```html
        <p><a href="{% url 'dashboard_home' %}">Voltar</a></p>
```
with:
```html
        <p><a href="{% url 'billing:subscribe_choose_plan' %}">Assinar agora</a></p>
        <p><a href="{% url 'dashboard_home' %}">Voltar</a></p>
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `docker compose run --rm web python manage.py test billing -v 2`
Expected: `OK` (56 tests — this also fixes the two tests left failing at the end of Task 7)

- [ ] **Step 9: Commit**

```bash
git add billing/forms.py billing/views.py billing/urls.py billing/tests.py templates/registration/crypto_payment_detail.html templates/registration/subscribe_required.html
git commit -m "feat(billing): add crypto payment confirmation screen"
```

---

### Task 9: Pending-payment expiry

**Files:**
- Modify: `billing/tasks.py`
- Modify: `smr/settings.py`
- Modify: `billing/tests.py`

**Interfaces:**
- Consumes: `billing.models.CryptoPayment`
- Produces: `billing.tasks.expire_crypto_payments() -> int` (Celery task, returns count expired).

- [ ] **Step 1: Write the failing tests**

`billing/tests.py` — update the tasks import line to:
```python
from billing.tasks import expire_crypto_payments, expire_subscriptions
```

Add the test class:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm web python manage.py test billing.tests.ExpireCryptoPaymentsTaskTest -v 2`
Expected: `FAIL` / `ImportError: cannot import name 'expire_crypto_payments' from 'billing.tasks'`

- [ ] **Step 3: Write the task**

`billing/tasks.py` — add:
```python
from billing.models import CryptoPayment


@shared_task
def expire_crypto_payments() -> int:
    return CryptoPayment.objects.filter(
        status=CryptoPayment.Status.PENDING,
        expires_at__lt=timezone.now(),
    ).update(status=CryptoPayment.Status.EXPIRED)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm web python manage.py test billing.tests.ExpireCryptoPaymentsTaskTest -v 2`
Expected: `OK` (3 tests)

- [ ] **Step 5: Schedule it**

In `smr/settings.py`, add to `CELERY_BEAT_SCHEDULE` (after `billing-expire-subscriptions-every-1h`):
```python
    "billing-expire-crypto-payments-every-5m": {
        "task": "billing.tasks.expire_crypto_payments",
        "schedule": 5 * 60,  # 5 minutes in seconds — pending window is short (30min default)
        "options": {"queue": "billing"},
    },
```

- [ ] **Step 6: Run the full billing suite**

Run: `docker compose run --rm web python manage.py test billing -v 2`
Expected: `OK` (59 tests)

- [ ] **Step 7: Commit**

```bash
git add billing/tasks.py billing/tests.py smr/settings.py
git commit -m "feat(billing): expire unpaid crypto payment intents"
```

---

### Task 10: Full-suite check, docs, and wrap-up

**Files:**
- Modify: `CLAUDE.md`
- No code changes

**Interfaces:** none — this task only verifies and documents.

- [ ] **Step 1: Update the project structure doc**

In `CLAUDE.md`, under "## Estrutura do projeto", update the `billing/` line's comment (currently `# cliente, assinatura, favoritos, credencial de corretora (placeholder)`) to also mention crypto billing:
```
├── billing/                   # cliente, assinatura, favoritos, credencial de corretora (placeholder), billing cripto TRC-20
```

- [ ] **Step 2: Run the full test suite**

Run: `docker compose run --rm web python manage.py test`
Expected: `OK` — every app's tests pass, including `billing` (59 tests), `accounts` (2 tests), and `bridge` (8 tests) — 69 total.

- [ ] **Step 3: Run Django's system check**

Run: `docker compose run --rm web python manage.py check`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: mention TRC-20 crypto billing in project structure"
```
