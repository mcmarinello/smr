# Freemium / Paywall UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the multi-tenant foundation's subscription-gating primitives (built but never used by any dashboard view) into the `dashboard` app: free customers see KPIs and the discovery ranking with wallet addresses masked; six deeper views (wallet profile, watchlist, alerts, settings, whale-copy status) require an active subscription; clicking a masked address shows an upsell popup instead of navigating.

**Architecture:** No new app, no new models. `billing/access.py` gets one new boolean helper reused by two dashboard views for context. `dashboard/views.py`'s six "deep" views switch from `@login_required` to the already-existing `@subscription_required` decorator. A new Django template filter masks wallet addresses. `dashboard/templates/dashboard/base.html` gets a self-contained modal (inline CSS/JS, no new dependency) shared by every page that extends it.

**Tech Stack:** Django 5.2.16 (template tags/filters, `TestCase`), vanilla JavaScript (no framework — none exists in the dashboard today).

## Global Constraints

- Código em inglês, UI em pt-BR — Python identifiers/comments in English, all template copy and popup text in Portuguese.
- Staff roles (`admin`, `operator`, `viewer`) always see everything, in both the free views (no masking) and the six gated views (never redirected) — this reuses the exact same rule already enforced by `billing.access.access_redirect`.
- Masking applies only to `dashboard_home` and `discovery_ranking` — these two stay reachable without a subscription, just with the wallet address masked and non-navigable. All other dashboard views are fully gated (redirect to `billing:subscribe_required` when accessed without an active subscription).
- The popup is only for masked-address click points (`.js-paywall-trigger`). Sidebar navigation links to gated pages are unchanged — clicking them still navigates and relies on the existing server-side `subscription_required` redirect.
- No new CSS framework or JS library — inline `<style>`/`<script>` in `base.html`, matching the pattern already used in `templates/registration/*.html`.
- Follow existing repo conventions: `TestCase` (see `billing/tests.py`, `bridge/tests.py`), pt-BR template copy matching the existing dark theme (`#0f1117`/`#1a1d27`/`#4f9eff`).

---

### Task 1: `has_full_access` helper

**Files:**
- Modify: `billing/access.py`
- Modify: `billing/tests.py`

**Interfaces:**
- Produces: `billing.access.has_full_access(user) -> bool`. Tasks 3 and 4 depend on this exact name and signature.

- [ ] **Step 1: Write the failing test**

`billing/tests.py` — add to the `from billing.access import ...` import line (find the current line, likely `from billing.access import access_redirect`, and extend it):
```python
from billing.access import access_redirect, has_full_access
```

Add the test class:
```python
class HasFullAccessTest(TestCase):
    def test_staff_always_has_full_access(self):
        user = User.objects.create_user(username="staff4", password="x", role=User.Role.ADMIN)
        self.assertTrue(has_full_access(user))

    def test_free_customer_does_not_have_full_access(self):
        user = User.objects.create_user(username="free3", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user, status=CustomerProfile.Status.FREE)
        self.assertFalse(has_full_access(user))

    def test_active_customer_has_full_access(self):
        user = User.objects.create_user(username="active3", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user, status=CustomerProfile.Status.ACTIVE)
        self.assertTrue(has_full_access(user))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm web python manage.py test billing.tests.HasFullAccessTest -v 2`
Expected: `FAIL` / `ImportError: cannot import name 'has_full_access' from 'billing.access'`

- [ ] **Step 3: Write the helper**

`billing/access.py` — add at the end of the file:
```python
def has_full_access(user) -> bool:
    """True for staff roles and active-subscription customers alike."""
    return access_redirect(user) is None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm web python manage.py test billing.tests.HasFullAccessTest -v 2`
Expected: `OK` (3 tests)

- [ ] **Step 5: Run the full billing suite**

Run: `docker compose run --rm web python manage.py test billing -v 2`
Expected: `OK` (65 tests)

- [ ] **Step 6: Commit**

```bash
git add billing/access.py billing/tests.py
git commit -m "feat(billing): add has_full_access helper for freemium gating"
```

---

### Task 2: `mask_address` template filter

**Files:**
- Create: `dashboard/templatetags/__init__.py`
- Create: `dashboard/templatetags/dashboard_extras.py`
- Create: `dashboard/tests.py` content (currently a stub — you're filling it in, not appending to existing tests)

**Interfaces:**
- Produces: `{% load dashboard_extras %}` + `{{ value|mask_address }}` template filter. Task 4 depends on this filter existing and being loadable from `dashboard_home.html`/`discovery_ranking.html`.

- [ ] **Step 1: Write the failing test**

`dashboard/tests.py` — replace the current stub content entirely with:
```python
from django.template import Context, Template
from django.test import TestCase


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm web python manage.py test dashboard.tests.MaskAddressFilterTest -v 2`
Expected: `FAIL` / `TemplateSyntaxError: 'dashboard_extras' is not a registered tag library`

- [ ] **Step 3: Write the template tag module**

`dashboard/templatetags/__init__.py`:
```python
```

`dashboard/templatetags/dashboard_extras.py`:
```python
from __future__ import annotations

from django import template

register = template.Library()


@register.filter
def mask_address(address: str) -> str:
    if len(address) <= 10:
        return address
    return f"{address[:6]}••••{address[-4:]}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm web python manage.py test dashboard.tests.MaskAddressFilterTest -v 2`
Expected: `OK` (3 tests)

Note the exact expected value for `"0x" + "b" * 40` (42 chars): first 6 chars are `0xbbbb`, last 4 chars are `bbbb`, joined with `••••` → `"0xbbbb••••bbbb"`.

- [ ] **Step 5: Commit**

```bash
git add dashboard/templatetags/ dashboard/tests.py
git commit -m "feat(dashboard): add mask_address template filter"
```

---

### Task 3: Gate the six paid dashboard views

**Files:**
- Modify: `dashboard/views.py`
- Modify: `dashboard/tests.py`

**Interfaces:**
- Consumes: `billing.decorators.subscription_required`
- Produces: `wallet_profile`, `watchlist`, `alerts_history`, `settings_page`, `whale_copy_status`, `whale_copy_api_status` all require an active subscription (or staff role). `dashboard_home` and `discovery_ranking` are untouched by this task (stay `@login_required` only — Task 4 adds masking to them, not gating).

- [ ] **Step 1: Write the failing test**

`dashboard/tests.py` — add imports:
```python
from django.urls import reverse

from accounts.models import User
from billing.models import CustomerProfile
from wallets.models import Wallet
```

Add the test class:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm web python manage.py test dashboard.tests.DashboardGatingTest -v 2`
Expected: `FAIL` — `test_free_customer_redirected_from_all_gated_views` fails because the views currently only require login, not an active subscription (a free customer gets `200`, not a redirect to `/assine/`).

- [ ] **Step 3: Gate the six views**

`dashboard/views.py` — replace the import line:
```python
from django.contrib.auth.decorators import login_required
```
with:
```python
from billing.decorators import subscription_required
```

Then replace the `@login_required` decorator on exactly these six view functions with `@subscription_required`: `wallet_profile`, `watchlist`, `alerts_history`, `settings_page`, `whale_copy_status`, `whale_copy_api_status`. Leave `dashboard_home` and `discovery_ranking` on `@login_required` — since you just removed the `login_required` import, add it back as a second import line instead of replacing:
```python
from django.contrib.auth.decorators import login_required
from billing.decorators import subscription_required
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm web python manage.py test dashboard.tests.DashboardGatingTest -v 2`
Expected: `OK` (3 tests)

- [ ] **Step 5: Run the full dashboard suite**

Run: `docker compose run --rm web python manage.py test dashboard -v 2`
Expected: `OK` (6 tests)

- [ ] **Step 6: Commit**

```bash
git add dashboard/views.py dashboard/tests.py
git commit -m "feat(dashboard): gate wallet profile, watchlist, alerts, settings, and whale-copy views behind subscription"
```

---

### Task 4: Mask wallet addresses in the two free views

**Files:**
- Modify: `dashboard/views.py`
- Modify: `dashboard/templates/dashboard/dashboard_home.html`
- Modify: `dashboard/templates/dashboard/discovery_ranking.html`
- Modify: `dashboard/tests.py`

**Interfaces:**
- Consumes: `billing.access.has_full_access`, `dashboard_extras.mask_address`
- Produces: `dashboard_home` and `discovery_ranking` context gains `has_full_access`; their templates render either a real link or a `.js-paywall-trigger` masked span depending on it. Task 5's popup JS depends on the `.js-paywall-trigger` class existing in the rendered HTML.

- [ ] **Step 1: Write the failing tests**

`dashboard/tests.py` — add imports:
```python
from django.utils import timezone

from alerts.models import Notification
from wallets.models import WalletScore, Window
```

Add the test class:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm web python manage.py test dashboard.tests.DashboardFreemiumMaskingTest -v 2`
Expected: `FAIL` — no `js-paywall-trigger` class exists anywhere yet, so both "free" tests fail on `assertContains`.

- [ ] **Step 3: Add `has_full_access` to both views' context**

`dashboard/views.py` — add to the imports:
```python
from billing.access import has_full_access
```

In `dashboard_home`, add to the `context` dict (before `return render(...)`):
```python
        "has_full_access": has_full_access(request.user),
```

In `discovery_ranking`, add the same key to its `context` dict.

- [ ] **Step 4: Update the templates**

`dashboard/templates/dashboard/discovery_ranking.html` — add `{% load dashboard_extras %}` as the first line of the file (before `{% extends %}` — Django allows `{% load %}` before `{% extends %}` at the top). Replace:
```html
          <td>
            <a class="addr mono" href="{% url 'wallet_profile' s.wallet.address %}">{{ s.wallet.address|truncatechars:14 }}</a>
          </td>
```
with:
```html
          <td>
            {% if has_full_access %}
              <a class="addr mono" href="{% url 'wallet_profile' s.wallet.address %}">{{ s.wallet.address|truncatechars:14 }}</a>
            {% else %}
              <a href="#" class="addr mono js-paywall-trigger">{{ s.wallet.address|mask_address }}</a>
            {% endif %}
          </td>
```

`dashboard/templates/dashboard/dashboard_home.html` — add `{% load dashboard_extras %}` as the first line. Replace:
```html
          <td>
            {% if n.wallet %}
              <a class="addr mono" href="{% url 'wallet_profile' n.wallet.address %}">{{ n.wallet.address|truncatechars:12 }}</a>
            {% else %}—{% endif %}
          </td>
```
with:
```html
          <td>
            {% if n.wallet and has_full_access %}
              <a class="addr mono" href="{% url 'wallet_profile' n.wallet.address %}">{{ n.wallet.address|truncatechars:12 }}</a>
            {% elif n.wallet %}
              <a href="#" class="addr mono js-paywall-trigger">{{ n.wallet.address|mask_address }}</a>
            {% else %}—{% endif %}
          </td>
```

- [ ] **Step 5: Run test to verify it passes**

Run: `docker compose run --rm web python manage.py test dashboard.tests.DashboardFreemiumMaskingTest -v 2`
Expected: `OK` (4 tests)

- [ ] **Step 6: Run the full dashboard suite**

Run: `docker compose run --rm web python manage.py test dashboard -v 2`
Expected: `OK` (10 tests)

- [ ] **Step 7: Commit**

```bash
git add dashboard/views.py dashboard/templates/dashboard/dashboard_home.html dashboard/templates/dashboard/discovery_ranking.html dashboard/tests.py
git commit -m "feat(dashboard): mask wallet addresses for free customers"
```

---

### Task 5: Upsell popup

**Files:**
- Modify: `dashboard/templates/dashboard/base.html`
- Modify: `dashboard/tests.py`

**Interfaces:**
- Produces: a `#paywall-modal` element present on every page extending `base.html`, plus a `click` listener that intercepts `.js-paywall-trigger` elements. No Python-level interface — this is a template/JS-only task, verified by asserting the modal markup renders.

- [ ] **Step 1: Write the failing test**

`dashboard/tests.py` — add:
```python
class PaywallModalTest(TestCase):
    def test_modal_markup_present_on_dashboard_home(self):
        user = User.objects.create_user(username="modaluser", password="x", role=User.Role.ADMIN)
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard_home"))

        self.assertContains(response, 'id="paywall-modal"')
        self.assertContains(response, "js-paywall-trigger")
```

Note: this test uses a staff (`ADMIN`) user just to reach `dashboard_home` easily — the modal markup itself is unconditional (rendered in `base.html` for every page, regardless of the viewer's access level), only whether any `.js-paywall-trigger` elements exist in the *body* depends on masking. Since `dashboard_home` renders the modal's own trigger button/script unconditionally as static markup in `base.html`, this assertion is really checking `base.html` structure, not per-user masking (Task 4 already covers the masking behavior itself).

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm web python manage.py test dashboard.tests.PaywallModalTest -v 2`
Expected: `FAIL` / `AssertionError` — no `id="paywall-modal"` in the response yet.

- [ ] **Step 3: Add the modal to `base.html`**

`dashboard/templates/dashboard/base.html` — add inside `<head>`, after the existing `<link rel="stylesheet" ...>` line:
```html
<style>
.paywall-modal { position: fixed; inset: 0; background: rgba(15,17,23,0.85); z-index: 1000; align-items: center; justify-content: center; }
.paywall-modal__box { background: #1a1d27; padding: 32px; border-radius: 12px; max-width: 360px; text-align: center; }
.paywall-modal__box h2 { color: #4f9eff; margin-top: 0; font-size: 20px; }
.paywall-modal__cta { display: block; margin-top: 20px; padding: 12px; background: #4f9eff; color: white; border-radius: 8px; text-decoration: none; font-weight: 600; }
.paywall-modal__close { margin-top: 12px; background: none; border: none; color: #9ca3af; cursor: pointer; font-size: 14px; }
</style>
```

Add just before the closing `</body>` tag:
```html
<div id="paywall-modal" class="paywall-modal" style="display:none">
  <div class="paywall-modal__box">
    <h2>Essa informação é para assinantes</h2>
    <p>Assine o SMR para ver os detalhes completos dessa carteira.</p>
    <a class="paywall-modal__cta" href="{% url 'billing:subscribe_choose_plan' %}">Assinar agora</a>
    <button type="button" id="paywall-modal-close" class="paywall-modal__close">Fechar</button>
  </div>
</div>
<script>
document.addEventListener("click", function (event) {
  var trigger = event.target.closest(".js-paywall-trigger");
  if (trigger) {
    event.preventDefault();
    document.getElementById("paywall-modal").style.display = "flex";
    return;
  }
  if (event.target.id === "paywall-modal-close") {
    document.getElementById("paywall-modal").style.display = "none";
  }
});
</script>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm web python manage.py test dashboard.tests.PaywallModalTest -v 2`
Expected: `OK` (1 test) — note the second assertion (`js-paywall-trigger`) will only pass if `dashboard_home`'s own markup happens to contain that class already from Task 4's conditional rendering for this particular staff user. Staff always has full access, so `dashboard_home` for this ADMIN user will NOT render any `.js-paywall-trigger` element in the body. Fix the test in this step to only assert the modal container, not the trigger class:

```python
class PaywallModalTest(TestCase):
    def test_modal_markup_present_on_dashboard_home(self):
        user = User.objects.create_user(username="modaluser", password="x", role=User.Role.ADMIN)
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard_home"))

        self.assertContains(response, 'id="paywall-modal"')
        self.assertContains(response, 'id="paywall-modal-close"')
```

Re-run: `docker compose run --rm web python manage.py test dashboard.tests.PaywallModalTest -v 2`
Expected: `OK` (1 test)

- [ ] **Step 5: Run the full dashboard suite**

Run: `docker compose run --rm web python manage.py test dashboard -v 2`
Expected: `OK` (11 tests)

- [ ] **Step 6: Commit**

```bash
git add dashboard/templates/dashboard/base.html dashboard/tests.py
git commit -m "feat(dashboard): add upsell popup for masked wallet addresses"
```

---

### Task 6: Full-suite check, docs, and wrap-up

**Files:**
- Modify: `CLAUDE.md`
- No code changes

**Interfaces:** none — this task only verifies and documents.

- [ ] **Step 1: Update the project structure doc**

In `CLAUDE.md`, under "## Estrutura do projeto", update the `dashboard/` line to mention freemium gating:
```
├── dashboard/                 # views agregadas, KPIs, whale_copy_status, gating freemium
```

- [ ] **Step 2: Run the full test suite**

Run: `docker compose run --rm web python manage.py test`
Expected: `OK` — every app's tests pass, including `billing` (65), `dashboard` (11), `accounts` (2), `bridge` (8) — 86 total.

- [ ] **Step 3: Run Django's system check**

Run: `docker compose run --rm web python manage.py check`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: mention freemium gating in project structure"
```
