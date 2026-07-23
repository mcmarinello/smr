# Multi-tenant Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn SMR from a single-tenant internal tool into a platform that can register paying customers — a `CustomerProfile` with subscription status, a placeholder for their exchange credentials, per-user favorites, self-service signup with (currently inert) e-mail verification, and view-level gating that blocks non-active customers.

**Architecture:** New Django app `billing` holds all customer-facing models (`CustomerProfile`, `ExchangeCredential`, `Favorite`) and the access-control primitives (`SubscriptionRequiredMixin`, `subscription_required` decorator) that later specs (Stripe, TRC-20, freemium UX, multi-broker copy execution) will build on. `accounts.User` gains a fourth role, `CUSTOMER`, reusing the existing single auth system instead of a parallel one.

**Tech Stack:** Django 5.2.16, Python 3.13, `cryptography` (Fernet) for at-rest encryption of exchange credentials, Celery (existing `django_celery_beat` schedule) for subscription expiry, Django's built-in `manage.py test` runner (`TestCase`, matches `bridge/tests.py` convention).

## Global Constraints

- Código em inglês, UI em pt-BR (CLAUDE.md) — all Python identifiers/comments in English, all template copy in Portuguese.
- Timestamps UTC; no timezone conversion outside presentation layer.
- Subscription status enum is exactly `free` / `active` / `expired` — no `trialing` in this plan.
- `EMAIL_VERIFICATION_REQUIRED` must default to `False` — verification is structurally complete but inert until a real e-mail provider is configured.
- `ExchangeCredential` stores no plaintext secrets ever, at rest or in memory beyond the request that encrypts them — this is schema-only, no connection/execution logic.
- Staff roles (`admin`, `operator`, `viewer`) always bypass subscription gating; gating only applies to `role=customer`.
- Favorites never change what the tracking/discovery engine monitors — purely a `User`–`Wallet` display filter.
- Follow the repo's existing per-app conventions: `BaseModel` from `wallets.models` for `created_at`/`updated_at`, `TestCase` + `override_settings` (see `bridge/tests.py`), one `tests.py` per app.

---

### Task 1: Bootstrap the `billing` app

**Files:**
- Create: `billing/__init__.py`
- Create: `billing/apps.py`
- Create: `billing/models.py` (empty placeholder, filled in Task 3)
- Create: `billing/admin.py` (empty placeholder)
- Create: `billing/tests.py`
- Modify: `smr/settings.py:12-35` (add `"billing"` to `INSTALLED_APPS`)
- Modify: `smr/settings.py:115-121` (add `"billing.*": {"queue": "billing"}` to `CELERY_TASK_ROUTES`)
- Modify: `requirements.txt` (add `cryptography==44.0.0`)

**Interfaces:**
- Produces: the `billing` app importable by later tasks (`billing.models`, `billing.admin`, `billing.tests`).

- [ ] **Step 1: Create the app skeleton**

`billing/__init__.py`:
```python
```

`billing/apps.py`:
```python
from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "billing"
```

`billing/models.py`:
```python
```

`billing/admin.py`:
```python
```

`billing/tests.py`:
```python
from django.test import TestCase
```

- [ ] **Step 2: Register the app and its Celery queue**

In `smr/settings.py`, add `"billing"` after `"bridge"` in `INSTALLED_APPS`:
```python
    "bridge",
    "billing",
    "dashboard",
```

In `smr/settings.py`, add a route for the new queue in `CELERY_TASK_ROUTES`:
```python
CELERY_TASK_ROUTES = {
    "discovery.*": {"queue": "discovery"},
    "tracking.*": {"queue": "tracking"},
    "wallets.tasks.*": {"queue": "scoring"},
    "alerts.*": {"queue": "alerts"},
    "copytrading.*": {"queue": "copytrading"},
    "billing.*": {"queue": "billing"},
}
```

- [ ] **Step 3: Add the encryption dependency**

In `requirements.txt`, add under "Django utilities":
```
django-extensions==4.1
cryptography==44.0.0
```

Run: `pip install cryptography==44.0.0`
Expected: installs cleanly (no output errors)

- [ ] **Step 4: Verify the app loads**

Run: `python3 manage.py check`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 5: Commit**

```bash
git add billing/ smr/settings.py requirements.txt
git commit -m "feat(billing): bootstrap billing app"
```

---

### Task 2: Add the `customer` role to `accounts.User`

**Files:**
- Modify: `accounts/models.py:1-19`
- Create: `accounts/migrations/0003_alter_user_role.py` (generated)
- Modify: `accounts/tests.py`

**Interfaces:**
- Produces: `User.Role.CUSTOMER` (value `"customer"`), `User.is_customer() -> bool`. Later tasks (`billing.models.CustomerProfile`, `billing.access`) depend on both.

- [ ] **Step 1: Write the failing test**

`accounts/tests.py`:
```python
from django.test import TestCase

from accounts.models import User


class UserRoleTest(TestCase):
    def test_customer_role_exists(self):
        user = User.objects.create_user(username="cliente1", password="x", role=User.Role.CUSTOMER)
        self.assertTrue(user.is_customer())
        self.assertFalse(user.is_admin())
        self.assertFalse(user.is_operator())

    def test_staff_roles_are_not_customer(self):
        user = User.objects.create_user(username="staff1", password="x", role=User.Role.VIEWER)
        self.assertFalse(user.is_customer())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 manage.py test accounts -v 2`
Expected: `FAIL` / `AttributeError: 'User' object has no attribute 'is_customer'` (or `AttributeError` on `User.Role.CUSTOMER`)

- [ ] **Step 3: Add the role and helper method**

`accounts/models.py`:
```python
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        OPERATOR = "operator", "Operator"
        VIEWER = "viewer", "Viewer"
        CUSTOMER = "customer", "Customer"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VIEWER)

    class Meta:
        db_table = "accounts_user"

    def is_admin(self) -> bool:
        return self.role == self.Role.ADMIN

    def is_operator(self) -> bool:
        return self.role in (self.Role.ADMIN, self.Role.OPERATOR)

    def is_customer(self) -> bool:
        return self.role == self.Role.CUSTOMER
```

- [ ] **Step 4: Generate and apply the migration**

Run: `python3 manage.py makemigrations accounts`
Expected: `Migrations for 'accounts': accounts/migrations/0003_alter_user_role.py - Alter field role on user`

Run: `python3 manage.py migrate accounts`
Expected: `Applying accounts.0003_alter_user_role... OK`

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 manage.py test accounts -v 2`
Expected: `OK` (2 tests)

- [ ] **Step 6: Commit**

```bash
git add accounts/models.py accounts/migrations/0003_alter_user_role.py accounts/tests.py
git commit -m "feat(accounts): add customer role"
```

---

### Task 3: `CustomerProfile` model

**Files:**
- Modify: `billing/models.py`
- Modify: `billing/admin.py`
- Create: `billing/migrations/0001_initial.py` (generated)
- Modify: `billing/tests.py`

**Interfaces:**
- Consumes: `accounts.models.User`, `wallets.models.BaseModel`
- Produces: `CustomerProfile` with fields `user` (OneToOne), `status` (`CustomerProfile.Status.FREE|ACTIVE|EXPIRED`), `plan_interval` (`CustomerProfile.Interval.MONTHLY|ANNUAL`, nullable), `current_period_end` (nullable datetime), `email_verified` (bool, default `False`). Accessible from a user as `user.customer_profile`. Later tasks (Task 6 signup, Task 7 verification, Task 8 gating, Task 9 expiry) depend on this exact shape.

- [ ] **Step 1: Write the failing test**

`billing/tests.py`:
```python
from django.test import TestCase

from accounts.models import User
from billing.models import CustomerProfile


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 manage.py test billing -v 2`
Expected: `FAIL` / `ImportError: cannot import name 'CustomerProfile' from 'billing.models'`

- [ ] **Step 3: Write the model**

`billing/models.py`:
```python
from __future__ import annotations

from django.db import models

from accounts.models import User
from wallets.models import BaseModel


class CustomerProfile(BaseModel):
    class Status(models.TextChoices):
        FREE = "free", "Free"
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"

    class Interval(models.TextChoices):
        MONTHLY = "monthly", "Mensal"
        ANNUAL = "annual", "Anual"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="customer_profile")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.FREE)
    plan_interval = models.CharField(max_length=20, choices=Interval.choices, null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    email_verified = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"{self.user.username} ({self.status})"
```

- [ ] **Step 4: Register in admin**

`billing/admin.py`:
```python
from django.contrib import admin

from billing.models import CustomerProfile


@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "status", "plan_interval", "current_period_end", "email_verified")
    list_filter = ("status", "plan_interval", "email_verified")
    search_fields = ("user__username", "user__email")
    raw_id_fields = ("user",)
```

- [ ] **Step 5: Generate and apply the migration**

Run: `python3 manage.py makemigrations billing`
Expected: `Migrations for 'billing': billing/migrations/0001_initial.py - Create model CustomerProfile`

Run: `python3 manage.py migrate billing`
Expected: `Applying billing.0001_initial... OK`

- [ ] **Step 6: Run test to verify it passes**

Run: `python3 manage.py test billing -v 2`
Expected: `OK` (2 tests)

- [ ] **Step 7: Commit**

```bash
git add billing/models.py billing/admin.py billing/migrations/0001_initial.py billing/tests.py
git commit -m "feat(billing): add CustomerProfile model"
```

---

### Task 4: `ExchangeCredential` model + at-rest encryption

**Files:**
- Create: `billing/crypto.py`
- Modify: `billing/models.py`
- Modify: `billing/admin.py`
- Modify: `smr/settings.py` (add `EXCHANGE_CREDENTIAL_ENCRYPTION_KEY`)
- Create: `billing/migrations/0002_exchangecredential.py` (generated)
- Modify: `billing/tests.py`

**Interfaces:**
- Consumes: `settings.EXCHANGE_CREDENTIAL_ENCRYPTION_KEY`
- Produces: `encrypt_secret(plain: str) -> str`, `decrypt_secret(token: str) -> str` (`billing/crypto.py`); `ExchangeCredential` model with `set_api_key`, `set_api_secret`, `get_api_key`, `get_api_secret` methods. Task 11 (exchange credential form) depends on these method names.

- [ ] **Step 1: Add the encryption key setting**

In `smr/settings.py`, after the `AUTH_PASSWORD_VALIDATORS` block, add:
```python
# Billing — at-rest encryption for exchange credentials (PRD: multi-tenant
# foundation spec, docs/specs/2026-07-23-multi-tenant-foundation-design.md).
# Dev default is deterministic from SECRET_KEY so local/test runs work with
# zero setup; production MUST override with a dedicated random Fernet key.
import base64
import hashlib

_dev_encryption_key = base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY.encode()).digest()).decode()
EXCHANGE_CREDENTIAL_ENCRYPTION_KEY = config(
    "EXCHANGE_CREDENTIAL_ENCRYPTION_KEY", default=_dev_encryption_key
)
```

- [ ] **Step 2: Write the failing test for crypto helpers**

`billing/tests.py` — add:
```python
from billing.crypto import decrypt_secret, encrypt_secret


class CryptoTest(TestCase):
    def test_round_trip(self):
        ciphertext = encrypt_secret("my-api-secret")
        self.assertNotEqual(ciphertext, "my-api-secret")
        self.assertEqual(decrypt_secret(ciphertext), "my-api-secret")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 manage.py test billing.tests.CryptoTest -v 2`
Expected: `FAIL` / `ModuleNotFoundError: No module named 'billing.crypto'`

- [ ] **Step 4: Write the crypto helpers**

`billing/crypto.py`:
```python
from __future__ import annotations

from cryptography.fernet import Fernet
from django.conf import settings


def _fernet() -> Fernet:
    return Fernet(settings.EXCHANGE_CREDENTIAL_ENCRYPTION_KEY.encode())


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 manage.py test billing.tests.CryptoTest -v 2`
Expected: `OK` (1 test)

- [ ] **Step 6: Write the failing test for the model**

`billing/tests.py` — add:
```python
from billing.models import ExchangeCredential


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
```

- [ ] **Step 7: Run test to verify it fails**

Run: `python3 manage.py test billing.tests.ExchangeCredentialTest -v 2`
Expected: `FAIL` / `ImportError: cannot import name 'ExchangeCredential' from 'billing.models'`

- [ ] **Step 8: Add the model**

`billing/models.py` — add at the end:
```python
from billing.crypto import decrypt_secret, encrypt_secret


class ExchangeCredential(BaseModel):
    """
    Schema-only placeholder (multi-tenant foundation spec). No connection or
    order-execution logic reads this yet — that is the multi-broker copy
    trading spec's job.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="exchange_credentials")
    exchange = models.CharField(max_length=20)  # "binance", "bybit", ...
    api_key_encrypted = models.TextField()
    api_secret_encrypted = models.TextField()
    is_active = models.BooleanField(default=True)

    def set_api_key(self, plain: str) -> None:
        self.api_key_encrypted = encrypt_secret(plain)

    def set_api_secret(self, plain: str) -> None:
        self.api_secret_encrypted = encrypt_secret(plain)

    def get_api_key(self) -> str:
        return decrypt_secret(self.api_key_encrypted)

    def get_api_secret(self) -> str:
        return decrypt_secret(self.api_secret_encrypted)

    def __str__(self) -> str:
        return f"{self.user.username} — {self.exchange}"
```

Move the `from billing.crypto import decrypt_secret, encrypt_secret` line to the top imports of `billing/models.py` alongside the other imports instead of inline — final import block:
```python
from __future__ import annotations

from django.db import models

from accounts.models import User
from billing.crypto import decrypt_secret, encrypt_secret
from wallets.models import BaseModel
```

- [ ] **Step 9: Register in admin**

`billing/admin.py` — add:
```python
from billing.models import CustomerProfile, ExchangeCredential


@admin.register(ExchangeCredential)
class ExchangeCredentialAdmin(admin.ModelAdmin):
    list_display = ("user", "exchange", "is_active", "created_at")
    list_filter = ("exchange", "is_active")
    search_fields = ("user__username",)
    raw_id_fields = ("user",)
    readonly_fields = ("api_key_encrypted", "api_secret_encrypted")
```

- [ ] **Step 10: Generate and apply the migration**

Run: `python3 manage.py makemigrations billing`
Expected: `Migrations for 'billing': billing/migrations/0002_exchangecredential.py - Create model ExchangeCredential`

Run: `python3 manage.py migrate billing`
Expected: `Applying billing.0002_exchangecredential... OK`

- [ ] **Step 11: Run test to verify it passes**

Run: `python3 manage.py test billing -v 2`
Expected: `OK` (4 tests)

- [ ] **Step 12: Commit**

```bash
git add billing/crypto.py billing/models.py billing/admin.py billing/migrations/0002_exchangecredential.py billing/tests.py smr/settings.py
git commit -m "feat(billing): add ExchangeCredential model with at-rest encryption"
```

---

### Task 5: `Favorite` model

**Files:**
- Modify: `billing/models.py`
- Modify: `billing/admin.py`
- Create: `billing/migrations/0003_favorite.py` (generated)
- Modify: `billing/tests.py`

**Interfaces:**
- Consumes: `wallets.models.Wallet`
- Produces: `Favorite` model, unique on `(user, wallet)`. Task 12 (`FavoriteToggleView`) depends on this.

- [ ] **Step 1: Write the failing test**

`billing/tests.py` — add:
```python
from django.db import IntegrityError

from billing.models import Favorite
from wallets.models import Wallet


class FavoriteTest(TestCase):
    def test_unique_per_user_and_wallet(self):
        user = User.objects.create_user(username="cliente5", password="x", role=User.Role.CUSTOMER)
        wallet = Wallet.objects.create(address="0x" + "a" * 40)
        Favorite.objects.create(user=user, wallet=wallet)
        with self.assertRaises(IntegrityError):
            Favorite.objects.create(user=user, wallet=wallet)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 manage.py test billing.tests.FavoriteTest -v 2`
Expected: `FAIL` / `ImportError: cannot import name 'Favorite' from 'billing.models'`

- [ ] **Step 3: Add the model**

`billing/models.py` — add at the end:
```python
from wallets.models import Wallet


class Favorite(BaseModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="favorites")
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="favorited_by")

    class Meta:
        unique_together = ("user", "wallet")

    def __str__(self) -> str:
        return f"{self.user.username} ★ {self.wallet.address}"
```

Move `from wallets.models import Wallet` into the shared `from wallets.models import BaseModel` import at the top instead of a new inline import — final import block in `billing/models.py`:
```python
from __future__ import annotations

from django.db import models

from accounts.models import User
from billing.crypto import decrypt_secret, encrypt_secret
from wallets.models import BaseModel, Wallet
```

- [ ] **Step 4: Register in admin**

`billing/admin.py` — add:
```python
from billing.models import CustomerProfile, ExchangeCredential, Favorite


@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ("user", "wallet", "created_at")
    search_fields = ("user__username", "wallet__address")
    raw_id_fields = ("user", "wallet")
```

- [ ] **Step 5: Generate and apply the migration**

Run: `python3 manage.py makemigrations billing`
Expected: `Migrations for 'billing': billing/migrations/0003_favorite.py - Create model Favorite`

Run: `python3 manage.py migrate billing`
Expected: `Applying billing.0003_favorite... OK`

- [ ] **Step 6: Run test to verify it passes**

Run: `python3 manage.py test billing -v 2`
Expected: `OK` (5 tests)

- [ ] **Step 7: Commit**

```bash
git add billing/models.py billing/admin.py billing/migrations/0003_favorite.py billing/tests.py
git commit -m "feat(billing): add Favorite model"
```

---

### Task 6: Signup form and view

**Files:**
- Create: `billing/forms.py`
- Modify: `billing/models.py` (no change — listed for context only)
- Create: `billing/views.py`
- Create: `billing/urls.py`
- Modify: `smr/urls.py`
- Create: `templates/registration/signup.html`
- Modify: `billing/tests.py`

**Interfaces:**
- Consumes: `accounts.models.User`, `billing.models.CustomerProfile`
- Produces: URL name `billing:signup`. Task 7 modifies `SignupView.form_valid` to also send the verification e-mail and change the redirect target.

- [ ] **Step 1: Write the failing test**

`billing/tests.py` — add:
```python
from django.urls import reverse


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
        self.assertRedirects(response, "/app/")

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 manage.py test billing.tests.SignupViewTest -v 2`
Expected: `FAIL` / `NoReverseMatch: 'billing' is not a registered namespace`

- [ ] **Step 3: Write the form**

`billing/forms.py`:
```python
from __future__ import annotations

from django import forms
from django.contrib.auth.forms import UserCreationForm

from accounts.models import User


class SignupForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")
```

- [ ] **Step 4: Write the view**

`billing/views.py`:
```python
from __future__ import annotations

from django.contrib.auth import login
from django.db import transaction
from django.urls import reverse_lazy
from django.views.generic import FormView

from accounts.models import User
from billing.forms import SignupForm
from billing.models import CustomerProfile


class SignupView(FormView):
    template_name = "registration/signup.html"
    form_class = SignupForm
    success_url = reverse_lazy("dashboard_home")

    def form_valid(self, form):
        with transaction.atomic():
            user = form.save(commit=False)
            user.role = User.Role.CUSTOMER
            user.save()
            CustomerProfile.objects.create(user=user)
        login(self.request, user)
        return super().form_valid(form)
```

- [ ] **Step 5: Wire the URLs**

`billing/urls.py`:
```python
from django.urls import path

from billing import views

app_name = "billing"

urlpatterns = [
    path("signup/", views.SignupView.as_view(), name="signup"),
]
```

In `smr/urls.py`, add the include next to the existing `login`/`logout` paths:
```python
urlpatterns = [
    path("", landing_page, name="landing"),
    path("app/", include("dashboard.urls")),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("billing.urls")),
    path("admin/", admin.site.urls),
    path("api/bridge/", include("bridge.urls")),
    path("", include("monitoring.urls")),
]
```

- [ ] **Step 6: Write the template**

`templates/registration/signup.html` (same visual language as `templates/registration/login.html`):
```html
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Criar conta — SMR</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f1117; color: #e0e0e0; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        .login-box { background: #1a1d27; padding: 40px; border-radius: 12px; width: 100%; max-width: 400px; box-shadow: 0 8px 32px rgba(0,0,0,0.4); }
        h1 { text-align: center; margin-bottom: 30px; color: #4f9eff; font-size: 24px; }
        .form-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; font-size: 14px; color: #9ca3af; }
        input[type=text], input[type=email], input[type=password] { width: 100%; padding: 12px; border: 1px solid #374151; border-radius: 8px; background: #0f1117; color: #e0e0e0; font-size: 16px; box-sizing: border-box; }
        input:focus { outline: none; border-color: #4f9eff; }
        button { width: 100%; padding: 12px; background: #4f9eff; color: white; border: none; border-radius: 8px; font-size: 16px; cursor: pointer; font-weight: 600; }
        button:hover { background: #3b82f6; }
        .error { color: #ef4444; font-size: 13px; margin-bottom: 12px; }
        .footer-link { text-align: center; margin-top: 16px; font-size: 14px; }
        .footer-link a { color: #4f9eff; text-decoration: none; }
    </style>
</head>
<body>
    <div class="login-box">
        <h1>🚀 Criar conta</h1>
        {% for field in form %}
            {% for error in field.errors %}<p class="error">{{ error }}</p>{% endfor %}
        {% endfor %}
        <form method="post">
            {% csrf_token %}
            <div class="form-group">
                <label for="id_username">Usuário</label>
                <input type="text" name="username" id="id_username" autofocus required>
            </div>
            <div class="form-group">
                <label for="id_email">E-mail</label>
                <input type="email" name="email" id="id_email" required>
            </div>
            <div class="form-group">
                <label for="id_password1">Senha</label>
                <input type="password" name="password1" id="id_password1" required>
            </div>
            <div class="form-group">
                <label for="id_password2">Confirmar senha</label>
                <input type="password" name="password2" id="id_password2" required>
            </div>
            <button type="submit">Criar conta</button>
        </form>
        <p class="footer-link"><a href="{% url 'login' %}">Já tenho conta</a></p>
    </div>
</body>
</html>
```

- [ ] **Step 7: Run test to verify it passes**

Run: `python3 manage.py test billing -v 2`
Expected: `OK` (7 tests)

- [ ] **Step 8: Commit**

```bash
git add billing/forms.py billing/views.py billing/urls.py billing/tests.py smr/urls.py templates/registration/signup.html
git commit -m "feat(billing): add customer signup"
```

---

### Task 7: E-mail verification (structured, inert by default)

**Files:**
- Create: `billing/tokens.py`
- Create: `billing/emails.py`
- Modify: `billing/views.py`
- Modify: `billing/urls.py`
- Modify: `smr/settings.py` (add `EMAIL_BACKEND`, `DEFAULT_FROM_EMAIL`)
- Create: `templates/registration/verify_email_sent.html`
- Create: `templates/registration/verify_email_result.html`
- Modify: `billing/tests.py`

**Interfaces:**
- Consumes: `billing.models.CustomerProfile.email_verified`
- Produces: `billing.tokens.email_verification_token` (a `PasswordResetTokenGenerator` subclass), `billing.emails.send_verification_email(user, request) -> None`, URL names `billing:verify_email_sent`, `billing:verify_email`. Task 8 (`access.py`) reads `CustomerProfile.email_verified` set here.

- [ ] **Step 1: Add e-mail settings**

In `smr/settings.py`, after the `TMT Bridge` section, add:
```python
# Billing — outbound e-mail (verification link, future receipts). No real
# provider configured yet: defaults to the console backend, which only logs
# the message and never fails, so signup is never blocked by this.
EMAIL_BACKEND = config("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="no-reply@smr.local")
```

- [ ] **Step 2: Write the failing test for the token generator**

`billing/tests.py` — add:
```python
from billing.tokens import email_verification_token


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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 manage.py test billing.tests.EmailVerificationTokenTest -v 2`
Expected: `FAIL` / `ModuleNotFoundError: No module named 'billing.tokens'`

- [ ] **Step 4: Write the token generator**

`billing/tokens.py`:
```python
from __future__ import annotations

from django.contrib.auth.tokens import PasswordResetTokenGenerator


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    """
    Same trick Django uses for password-reset tokens: the hash embeds
    mutable state (here, `email_verified`) so a token stops validating the
    moment it has been used once.
    """

    def _make_hash_value(self, user, timestamp):
        return f"{user.pk}{timestamp}{user.customer_profile.email_verified}"


email_verification_token = EmailVerificationTokenGenerator()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 manage.py test billing.tests.EmailVerificationTokenTest -v 2`
Expected: `OK` (2 tests)

- [ ] **Step 6: Write the failing test for sending + verifying**

`billing/tests.py` — add:
```python
from django.core import mail

from billing.emails import send_verification_email
from billing.tokens import email_verification_token
from django.test import RequestFactory
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode


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
```

- [ ] **Step 7: Run test to verify it fails**

Run: `python3 manage.py test billing.tests.SendVerificationEmailTest billing.tests.VerifyEmailViewTest -v 2`
Expected: `FAIL` / `ModuleNotFoundError: No module named 'billing.emails'`

- [ ] **Step 8: Write the email helper**

`billing/emails.py`:
```python
from __future__ import annotations

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from billing.tokens import email_verification_token


def send_verification_email(user, request) -> None:
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = email_verification_token.make_token(user)
    path = reverse("billing:verify_email", kwargs={"uidb64": uidb64, "token": token})
    verify_url = request.build_absolute_uri(path)
    send_mail(
        subject="Confirme seu e-mail — SMR",
        message=f"Clique para confirmar seu cadastro: {verify_url}",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )
```

- [ ] **Step 9: Add the verification views**

`billing/views.py` — add imports and views, and update `SignupView`:
```python
from django.shortcuts import render
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views import View
from django.views.generic import TemplateView

from billing.emails import send_verification_email
from billing.tokens import email_verification_token


class SignupView(FormView):
    template_name = "registration/signup.html"
    form_class = SignupForm
    success_url = reverse_lazy("billing:verify_email_sent")

    def form_valid(self, form):
        with transaction.atomic():
            user = form.save(commit=False)
            user.role = User.Role.CUSTOMER
            user.save()
            CustomerProfile.objects.create(user=user)
        send_verification_email(user, self.request)
        login(self.request, user)
        return super().form_valid(form)


class VerifyEmailSentView(TemplateView):
    template_name = "registration/verify_email_sent.html"


class VerifyEmailView(View):
    def get(self, request, uidb64, token):
        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)
        except (User.DoesNotExist, ValueError, TypeError, OverflowError):
            user = None

        if user is not None and email_verification_token.check_token(user, token):
            user.customer_profile.email_verified = True
            user.customer_profile.save(update_fields=["email_verified"])
            return render(request, "registration/verify_email_result.html", {"success": True})
        return render(request, "registration/verify_email_result.html", {"success": False}, status=400)
```

(`SignupView`'s new `success_url` replaces the one written in Task 6.)

- [ ] **Step 10: Update the Task 6 tests for the new redirect target**

`billing/tests.py` — replace the two assertions in `SignupViewTest` that referenced `/app/`:
```python
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
```
(`test_signup_logs_the_user_in` is unaffected — leave it as written in Task 6.)

- [ ] **Step 11: Wire the URLs**

`billing/urls.py`:
```python
from django.urls import path

from billing import views

app_name = "billing"

urlpatterns = [
    path("signup/", views.SignupView.as_view(), name="signup"),
    path("signup/confirme-seu-email/", views.VerifyEmailSentView.as_view(), name="verify_email_sent"),
    path("verificar-email/<str:uidb64>/<str:token>/", views.VerifyEmailView.as_view(), name="verify_email"),
]
```

- [ ] **Step 12: Write the templates**

`templates/registration/verify_email_sent.html`:
```html
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Confirme seu e-mail — SMR</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f1117; color: #e0e0e0; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; text-align: center; }
        .box { background: #1a1d27; padding: 40px; border-radius: 12px; max-width: 420px; }
        h1 { color: #4f9eff; }
        a { color: #4f9eff; }
    </style>
</head>
<body>
    <div class="box">
        <h1>Quase lá!</h1>
        <p>Enviamos um link de confirmação para o seu e-mail. Clique nele para verificar sua conta.</p>
        <p><a href="{% url 'dashboard_home' %}">Continuar por enquanto</a></p>
    </div>
</body>
</html>
```

`templates/registration/verify_email_result.html`:
```html
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Verificação de e-mail — SMR</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f1117; color: #e0e0e0; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; text-align: center; }
        .box { background: #1a1d27; padding: 40px; border-radius: 12px; max-width: 420px; }
        h1 { color: {% if success %}#22c55e{% else %}#ef4444{% endif %}; }
        a { color: #4f9eff; }
    </style>
</head>
<body>
    <div class="box">
        {% if success %}
            <h1>E-mail confirmado!</h1>
            <p>Sua conta foi verificada com sucesso.</p>
        {% else %}
            <h1>Link inválido</h1>
            <p>Esse link de verificação é inválido ou expirou.</p>
        {% endif %}
        <p><a href="{% url 'dashboard_home' %}">Ir para o painel</a></p>
    </div>
</body>
</html>
```

- [ ] **Step 13: Run test to verify it passes**

Run: `python3 manage.py test billing -v 2`
Expected: `OK` (12 tests)

- [ ] **Step 14: Commit**

```bash
git add billing/tokens.py billing/emails.py billing/views.py billing/urls.py billing/tests.py smr/settings.py templates/registration/verify_email_sent.html templates/registration/verify_email_result.html
git commit -m "feat(billing): add email verification (inert by default)"
```

---

### Task 8: Subscription access gating

**Files:**
- Create: `billing/access.py`
- Create: `billing/mixins.py`
- Create: `billing/decorators.py`
- Modify: `billing/views.py`
- Modify: `billing/urls.py`
- Modify: `smr/settings.py` (add `EMAIL_VERIFICATION_REQUIRED`)
- Create: `templates/registration/subscribe_required.html`
- Modify: `billing/tests.py`

**Interfaces:**
- Consumes: `accounts.models.User.Role`, `billing.models.CustomerProfile.Status`, `settings.EMAIL_VERIFICATION_REQUIRED`
- Produces: `billing.access.access_redirect(user) -> str | None` (a URL *name*, not a path), `billing.mixins.SubscriptionRequiredMixin`, `billing.decorators.subscription_required`. Tasks 10 and 11 (`ExchangeCredentialCreateView`, `FavoriteToggleView`) use `SubscriptionRequiredMixin`.

- [ ] **Step 1: Add the settings flag**

In `smr/settings.py`, next to `EMAIL_BACKEND` (Task 7), add:
```python
# When True, a customer must have CustomerProfile.email_verified before
# accessing gated views. Defaults False until a real e-mail provider exists —
# see docs/specs/2026-07-23-multi-tenant-foundation-design.md.
EMAIL_VERIFICATION_REQUIRED = config("EMAIL_VERIFICATION_REQUIRED", default=False, cast=bool)
```

- [ ] **Step 2: Write the failing test for `access_redirect`**

`billing/tests.py` — add:
```python
from django.test import override_settings

from billing.access import access_redirect


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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 manage.py test billing.tests.AccessRedirectTest -v 2`
Expected: `FAIL` / `ModuleNotFoundError: No module named 'billing.access'`

- [ ] **Step 4: Write `access.py`**

`billing/access.py`:
```python
from __future__ import annotations

from django.conf import settings

from accounts.models import User
from billing.models import CustomerProfile


def access_redirect(user) -> str | None:
    """
    Returns the URL *name* to redirect a customer to if they should not see
    a gated view, or None if access is allowed. Staff roles never gated.
    """
    if user.role != User.Role.CUSTOMER:
        return None

    profile = user.customer_profile
    if settings.EMAIL_VERIFICATION_REQUIRED and not profile.email_verified:
        return "billing:verify_email_sent"
    if profile.status != CustomerProfile.Status.ACTIVE:
        return "billing:subscribe_required"
    return None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 manage.py test billing.tests.AccessRedirectTest -v 2`
Expected: `OK` (4 tests)

- [ ] **Step 6: Write the failing test for the mixin and decorator**

`billing/tests.py` — add:
```python
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
```

This test hits `/minhas-credenciais/`, the real URL Task 10 (`ExchangeCredentialCreateView`) will register. Until Task 10 exists, Step 7 below adds a temporary probe view + URL used only by this test class; Task 10 will replace that probe with the real view at the same path, so this test keeps passing unmodified.

- [ ] **Step 7: Add a temporary probe view to prove the mixin works**

`billing/views.py` — add:
```python
from django.http import HttpResponse

from billing.mixins import SubscriptionRequiredMixin
from django.views.generic import View as GenericView


class _GatedProbeView(SubscriptionRequiredMixin, GenericView):
    def get(self, request):
        return HttpResponse("ok")
```

`billing/urls.py` — add:
```python
    path("minhas-credenciais/", views._GatedProbeView.as_view(), name="gated_probe"),
```

(Task 10 removes `_GatedProbeView` and this URL line, replacing both with `ExchangeCredentialCreateView` at the same path.)

- [ ] **Step 8: Run test to verify it fails**

Run: `python3 manage.py test billing.tests.SubscriptionGatingViewTest -v 2`
Expected: `FAIL` / `ModuleNotFoundError: No module named 'billing.mixins'`

- [ ] **Step 9: Write the mixin and decorator**

`billing/mixins.py`:
```python
from __future__ import annotations

from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect

from billing.access import access_redirect


class SubscriptionRequiredMixin:
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        target = access_redirect(request.user)
        if target:
            return redirect(target)
        return super().dispatch(request, *args, **kwargs)
```

`billing/decorators.py`:
```python
from __future__ import annotations

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect

from billing.access import access_redirect


def subscription_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        target = access_redirect(request.user)
        if target:
            return redirect(target)
        return view_func(request, *args, **kwargs)

    return wrapped
```

- [ ] **Step 10: Write the subscribe-required view and template**

`billing/views.py` — add:
```python
class SubscribeRequiredView(TemplateView):
    template_name = "registration/subscribe_required.html"
```

`billing/urls.py` — add:
```python
    path("assine/", views.SubscribeRequiredView.as_view(), name="subscribe_required"),
```

`templates/registration/subscribe_required.html`:
```html
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Assine o SMR</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f1117; color: #e0e0e0; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; text-align: center; }
        .box { background: #1a1d27; padding: 40px; border-radius: 12px; max-width: 420px; }
        h1 { color: #4f9eff; }
        a { color: #4f9eff; }
    </style>
</head>
<body>
    <div class="box">
        <h1>Essa área é para assinantes</h1>
        <p>Assine o SMR para desbloquear essa funcionalidade.</p>
        <p><a href="{% url 'dashboard_home' %}">Voltar</a></p>
    </div>
</body>
</html>
```

(The upsell UX — pricing, popups, plan picker — is the freemium spec, not this one; this page is a functional stopgap.)

- [ ] **Step 11: Run test to verify it passes**

Run: `python3 manage.py test billing -v 2`
Expected: `OK` (20 tests)

- [ ] **Step 12: Commit**

```bash
git add billing/access.py billing/mixins.py billing/decorators.py billing/views.py billing/urls.py billing/tests.py smr/settings.py templates/registration/subscribe_required.html
git commit -m "feat(billing): add subscription access gating"
```

---

### Task 9: Subscription expiry Celery task

**Files:**
- Create: `billing/tasks.py`
- Modify: `smr/settings.py` (`CELERY_BEAT_SCHEDULE`)
- Modify: `billing/tests.py`

**Interfaces:**
- Consumes: `billing.models.CustomerProfile`
- Produces: `billing.tasks.expire_subscriptions() -> int` (Celery shared task, returns count of profiles expired).

- [ ] **Step 1: Write the failing test**

`billing/tests.py` — add:
```python
from datetime import timedelta

from django.utils import timezone

from billing.tasks import expire_subscriptions


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 manage.py test billing.tests.ExpireSubscriptionsTaskTest -v 2`
Expected: `FAIL` / `ModuleNotFoundError: No module named 'billing.tasks'`

- [ ] **Step 3: Write the task**

`billing/tasks.py`:
```python
from __future__ import annotations

from celery import shared_task
from django.utils import timezone

from billing.models import CustomerProfile


@shared_task
def expire_subscriptions() -> int:
    return CustomerProfile.objects.filter(
        status=CustomerProfile.Status.ACTIVE,
        current_period_end__lt=timezone.now(),
    ).update(status=CustomerProfile.Status.EXPIRED)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 manage.py test billing.tests.ExpireSubscriptionsTaskTest -v 2`
Expected: `OK` (3 tests)

- [ ] **Step 5: Schedule it**

In `smr/settings.py`, add to `CELERY_BEAT_SCHEDULE`:
```python
    "billing-expire-subscriptions-every-1h": {
        "task": "billing.tasks.expire_subscriptions",
        "schedule": 60 * 60,  # 1 hour in seconds
        "options": {"queue": "billing"},
    },
```

- [ ] **Step 6: Commit**

```bash
git add billing/tasks.py billing/tests.py smr/settings.py
git commit -m "feat(billing): expire subscriptions past their period end"
```

---

### Task 10: Exchange credential registration screen

**Files:**
- Modify: `billing/forms.py`
- Modify: `billing/views.py` (replace `_GatedProbeView` with the real view)
- Modify: `billing/urls.py`
- Create: `templates/registration/exchange_credential_form.html`
- Modify: `billing/tests.py`

**Interfaces:**
- Consumes: `billing.models.ExchangeCredential`, `billing.mixins.SubscriptionRequiredMixin`
- Produces: URL `billing:exchange_credential_create` at `/minhas-credenciais/` (same path Task 8's probe used, so `SubscriptionGatingViewTest` keeps passing against the real view).

- [ ] **Step 1: Update the gating test's expectations**

`billing/tests.py` — in `SubscriptionGatingViewTest`, change every `response = self.client.get("/minhas-credenciais/")` to also cover the POST-created credential flow is out of scope for this class (it stays a GET-only smoke test); no code change needed here since GET on a `FormView` still renders 200. Leave the four existing tests as-is — they should still pass once Step 3 replaces the probe view class name.

- [ ] **Step 2: Write the failing test for credential creation**

`billing/tests.py` — add:
```python
from billing.models import ExchangeCredential


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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 manage.py test billing.tests.ExchangeCredentialCreateViewTest -v 2`
Expected: `FAIL` / `NoReverseMatch: Reverse for 'exchange_credential_create' not found`

- [ ] **Step 4: Write the form**

`billing/forms.py` — add:
```python
class ExchangeCredentialForm(forms.Form):
    EXCHANGE_CHOICES = [("binance", "Binance"), ("bybit", "Bybit")]

    exchange = forms.ChoiceField(choices=EXCHANGE_CHOICES)
    api_key = forms.CharField(widget=forms.PasswordInput)
    api_secret = forms.CharField(widget=forms.PasswordInput)
```

- [ ] **Step 5: Replace the probe view with the real one**

`billing/views.py` — remove the `_GatedProbeView` class and its now-unused `from django.http import HttpResponse` import added in Task 8 Step 7. Keep the `from django.views.generic import View as GenericView` import even though nothing uses it yet — Task 11's `FavoriteToggleView` reuses it. Replace the removed class with:
```python
from django.urls import reverse_lazy

from billing.forms import ExchangeCredentialForm
from billing.models import ExchangeCredential


class ExchangeCredentialCreateView(SubscriptionRequiredMixin, FormView):
    template_name = "registration/exchange_credential_form.html"
    form_class = ExchangeCredentialForm
    success_url = reverse_lazy("dashboard_home")

    def form_valid(self, form):
        credential = ExchangeCredential(user=self.request.user, exchange=form.cleaned_data["exchange"])
        credential.set_api_key(form.cleaned_data["api_key"])
        credential.set_api_secret(form.cleaned_data["api_secret"])
        credential.save()
        return super().form_valid(form)
```

- [ ] **Step 6: Update the URL**

`billing/urls.py` — replace:
```python
    path("minhas-credenciais/", views._GatedProbeView.as_view(), name="gated_probe"),
```
with:
```python
    path("minhas-credenciais/", views.ExchangeCredentialCreateView.as_view(), name="exchange_credential_create"),
```

- [ ] **Step 7: Update the Task 8 gating tests to the new URL name usage**

`billing/tests.py` — `SubscriptionGatingViewTest` keeps using the literal path `"/minhas-credenciais/"`, which is unchanged, so no edits are required there. Confirm by re-running the full suite in Step 9.

- [ ] **Step 8: Write the template**

`templates/registration/exchange_credential_form.html`:
```html
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Conectar corretora — SMR</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f1117; color: #e0e0e0; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        .box { background: #1a1d27; padding: 40px; border-radius: 12px; width: 100%; max-width: 400px; }
        h1 { text-align: center; color: #4f9eff; }
        label { display: block; margin: 16px 0 8px; font-size: 14px; color: #9ca3af; }
        select, input { width: 100%; padding: 12px; border: 1px solid #374151; border-radius: 8px; background: #0f1117; color: #e0e0e0; box-sizing: border-box; }
        button { width: 100%; margin-top: 20px; padding: 12px; background: #4f9eff; color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; }
    </style>
</head>
<body>
    <div class="box">
        <h1>Conectar corretora</h1>
        <p>Suas chaves são criptografadas e nunca ficam visíveis depois de salvas.</p>
        <form method="post">
            {% csrf_token %}
            <label for="id_exchange">Corretora</label>
            {{ form.exchange }}
            <label for="id_api_key">API Key</label>
            {{ form.api_key }}
            <label for="id_api_secret">API Secret</label>
            {{ form.api_secret }}
            <button type="submit">Salvar</button>
        </form>
    </div>
</body>
</html>
```

- [ ] **Step 9: Run test to verify it passes**

Run: `python3 manage.py test billing -v 2`
Expected: `OK` (24 tests)

- [ ] **Step 10: Commit**

```bash
git add billing/forms.py billing/views.py billing/urls.py billing/tests.py templates/registration/exchange_credential_form.html
git commit -m "feat(billing): add exchange credential registration screen"
```

---

### Task 11: Favorite toggle endpoint

**Files:**
- Modify: `billing/views.py`
- Modify: `billing/urls.py`
- Modify: `billing/tests.py`

**Interfaces:**
- Consumes: `billing.models.Favorite`, `billing.mixins.SubscriptionRequiredMixin`, `wallets.models.Wallet`
- Produces: `POST /favoritos/<int:wallet_id>/` → URL name `billing:favorite_toggle`, JSON `{"favorited": bool}`.

- [ ] **Step 1: Write the failing test**

`billing/tests.py` — add:
```python
from billing.models import Favorite


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 manage.py test billing.tests.FavoriteToggleViewTest -v 2`
Expected: `FAIL` / `NoReverseMatch: Reverse for 'favorite_toggle' not found`

- [ ] **Step 3: Write the view**

`billing/views.py` — add:
```python
from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from billing.models import Favorite
from wallets.models import Wallet


class FavoriteToggleView(SubscriptionRequiredMixin, GenericView):
    def post(self, request, wallet_id):
        wallet = get_object_or_404(Wallet, pk=wallet_id)
        favorite, created = Favorite.objects.get_or_create(user=request.user, wallet=wallet)
        if not created:
            favorite.delete()
            return JsonResponse({"favorited": False})
        return JsonResponse({"favorited": True})
```

(`GenericView` is already imported from Task 8's `from django.views.generic import View as GenericView`; keep that import even though `_GatedProbeView` was removed in Task 10.)

- [ ] **Step 4: Wire the URL**

`billing/urls.py` — add:
```python
    path("favoritos/<int:wallet_id>/", views.FavoriteToggleView.as_view(), name="favorite_toggle"),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 manage.py test billing -v 2`
Expected: `OK` (26 tests)

- [ ] **Step 6: Commit**

```bash
git add billing/views.py billing/urls.py billing/tests.py
git commit -m "feat(billing): add favorite toggle endpoint"
```

---

### Task 12: Full-suite check, docs, and wrap-up

**Files:**
- Modify: `CLAUDE.md` (project structure tree)
- No code changes

**Interfaces:** none — this task only verifies and documents.

- [ ] **Step 1: Update the project structure doc**

In `CLAUDE.md`, under "## Estrutura do projeto", add the new app to the tree (keep alphabetical-ish grouping consistent with the existing list, right after `bridge/`):
```
├── bridge/                    # contrato de API com TMT (desligado)
├── billing/                   # cliente, assinatura, favoritos, credencial de corretora (placeholder)
├── dashboard/                 # views agregadas, KPIs, whale_copy_status
```

- [ ] **Step 2: Run the full test suite**

Run: `python3 manage.py test`
Expected: `OK` — every app's tests pass, including `billing` (26 tests) and the `accounts` addition (2 tests) from Task 2.

- [ ] **Step 3: Run Django's system check**

Run: `python3 manage.py check`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add billing app to project structure"
```
