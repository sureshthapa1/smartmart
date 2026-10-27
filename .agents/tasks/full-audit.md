# Smart Mart — Full Codebase Audit
**Date:** 2025-07-17  
**Project:** GoldKernel Smart Mart POS (Flask/Python)  
**Scope:** All blueprints, models, services, templates, config, and root-level scripts

---

## Executive Summary

The codebase is a mature, feature-rich POS application with generally good practices: CSRF protection is applied consistently, SQLAlchemy ORM is used throughout (no raw SQL injection risk), and there is a real error-handler hierarchy. The main original user complaint — a popup/sign appearing on every barcode scan — is traced below as a **CRITICAL** issue in the POS template's barcode input implementation using `contenteditable` instead of a real `<input>`, combined with Chrome's native "save password" or "autofill" UI triggering on that element.

Beyond the original bug, the audit found: **2 CRITICAL**, **7 HIGH**, **8 MEDIUM**, and **10 LOW** issues.

---

## CRITICAL

### C-1 — Barcode scanner popup on every scan (Original bug)
**File:** `smart_mart/templates/sales/create.html` (lines ~310–330)  
**Description:**  
The barcode input is implemented as a `<div contenteditable="true">` rather than a standard `<input type="text">`. While this was intentional (to prevent Chrome's password-manager from attaching to a text field named `barcode`), Chrome's autofill/form-assistant has extended its heuristics: it now triggers on `contenteditable` elements inside forms too, especially when the surrounding `<form>` contains password-type fields (the login link in the nav) or when Chrome detects rapid keyboard input resembling structured data (barcodes).  
Additionally, the element has no `autocomplete="off"` attribute and sits inside a `<form>` element, which signals to Chrome that it is an interactive form field eligible for autofill. The barcode beep sound confirms successful scans; the popup appears every time because Chrome's payment/autofill assistant fires on each `input` event.

**Evidence (template excerpt):**
```html
<div id="barcodeInput"
     contenteditable="true"
     role="textbox"
     aria-label="Scan barcode or type SKU"
     spellcheck="false"
     data-placeholder="Scan barcode / type SKU…"
     class="form-control form-control-sm"
     style="width:200px; cursor:text; overflow:hidden; white-space:nowrap;"></div>
```
No `autocomplete` attribute, no `inputmode`, inside a `<form>`. Chrome's autofill fires.

**Suggested fix:**  
- Switch to a real `<input type="search" autocomplete="off" id="barcodeInput">` and remove the contenteditable div. The `type="search"` prevents Chrome password manager specifically; `autocomplete="off"` suppresses autofill. To prevent Chrome from ignoring `autocomplete="off"`, also add `autocomplete="one-time-code"` or use a unique `name` like `barcode_scanner_input_ignore` that doesn't match any known autofill field.  
- Alternatively, keep the `contenteditable` div but move it **outside** the `<form>` tag and use JS to add items; this prevents Chrome from seeing it as a form element at all.

---

### C-2 — Plaintext credentials in `.env` committed to repo / accessible
**File:** `.env` (root)  
**Description:**  
The `.env` file contains several live credentials (values redacted from this report):
- `ADMIN_PASSWORD` — plaintext admin password
- `GEMINI_API_KEY` — active Gemini API key
- `PEXELS_API_KEY` — active Pexels API key
- `MAIL_PASSWORD` — Gmail app password (live account)
- `ESEWA_SECRET_KEY` — eSewa sandbox secret key
- `SECRET_KEY` — Flask session signing key

If this repo is ever pushed to a public or shared Git host, **all credentials are exposed**. The test suite also hardcodes `ESEWA_PUBLIC_SANDBOX_SECRET = "8gBm/:&EnhH.1/q"` in `tests/unit/test_store_checkout_payment.py` (acceptable for tests, but confirms the value is publicly known).

**Suggested fix:**  
- Add `.env` to `.gitignore` immediately if not already present.
- Rotate the Gmail app password and Gemini API key.
- Provide a `.env.example` with placeholder values only.

---

## HIGH

### H-1 — `serve.py` exposes raw Python tracebacks to browser clients
**File:** `serve.py` (lines 33–42)  
**Description:**  
The global error handler in `serve.py` returns a raw `traceback.format_exc()` string inside an HTML `<pre>` tag. Anyone who triggers a 500 error sees the full stack trace, including file paths, library versions, and potentially variable values. This is a significant information-disclosure vulnerability.
```python
return (
    "<pre style='padding:2rem;background:#111;color:#f87171;font-size:13px'>"
    f"500 — Internal Server Error\n\n{tb}</pre>"
), 500
```
**Suggested fix:**  
Log the traceback (already done), but return a generic user-facing error page. The app already registers `errors/500.html` in `app.py`; the `serve.py` handler overrides it with raw stack traces.

---

### H-2 — `update_payment_mode` route has no error handling around `db.session.commit()`
**File:** `smart_mart/blueprints/sales/routes.py` (lines 301–305)  
**Description:**  
```python
sale.payment_mode = new_mode
sale.payment_method = new_mode
db.session.commit()  # ← no try/except; DB errors propagate as unhandled 500
```
If the commit fails (connection drop, constraint violation) the session is left in a dirty state and the user gets a raw 500 with no rollback.

**Suggested fix:**  
Wrap in `try/except`, call `db.session.rollback()` in the except block, and flash an error message.

---

### H-3 — N+1 query pattern in `dead_stock_analysis()` — fires one DB query per product
**File:** `smart_mart/services/ai_trend_analyzer.py` (lines 105–116)  
**Description:**  
```python
for p in products:
    last_sale = db.session.execute(
        db.select(func.max(Sale.sale_date))
        .join(SaleItem, SaleItem.sale_id == Sale.id)
        .where(SaleItem.product_id == p.id)
    ).scalar()
```
For a store with 200 dead-stock products this fires 200 individual DB queries. The `fast_moving_products()` function in the same file correctly aggregates in a single query; `dead_stock_analysis()` should use the same pattern.

**Suggested fix:**  
Replace the loop with a single aggregate query that returns `MAX(sale_date)` grouped by `product_id`, then build a `{product_id: last_sale}` dict for O(1) lookups in the loop.

---

### H-4 — N+1 query in `ai_customer_intelligence.py` upsell calculation
**File:** `smart_mart/services/ai_customer_intelligence.py` (lines 223–237)  
**Description:**  
```python
for pid in list(bought_ids)[:3]:
    p = db.session.get(Product, pid)   # ← 1 query per product
    if p:
        premium = db.session.execute(...)  # ← 1 query per product
```
Up to 6 DB queries per call to `get_recommendations()`, called from the billing page on every customer lookup. At busy times this is a per-request performance drain.

**Suggested fix:**  
Load all needed products in one `db.session.execute(db.select(Product).where(Product.id.in_(bought_ids)))` call, then use a dict for lookups.

---

### H-5 — `bi_dashboard_bp` in `blueprints/bi_dashboard/__init__.py` is empty but still registered
**File:** `smart_mart/blueprints/bi_dashboard/__init__.py`  
**Description:**  
The file contains only `"""Bi Dashboard blueprint."""` (no routes, no blueprint object). The app attempts to register it by importing `bi_dashboard_bp` from `.bi.routes.api` instead of from this `blueprints/bi_dashboard` package — the two are separate. The `blueprints/bi_dashboard/` directory is dead code: it has no `routes.py`, no blueprint object. Its `__init__.py` exists but is unused. This caused a documented 500 `BuildError` in the test file `tests/unit/test_bi_dashboard_pages.py`.

**Suggested fix:**  
Delete `smart_mart/blueprints/bi_dashboard/` entirely. All BI dashboard routes live in `smart_mart/bi/routes/api.py` registered as `bi_dashboard_bp`. The directory is misleading and contributes nothing.

---

### H-6 — `delete_sale` and `void_sale` do not call `db.session.rollback()` on failure
**File:** `smart_mart/blueprints/sales/routes.py` (lines 595–598, 633–635)  
**Description:**  
```python
except Exception as e:
    flash(f"Error deleting sale: {e}", "danger")
return redirect(url_for("sales.list_sales"))
```
If `sales_manager.delete_sale()` fails mid-way (after some stock was already reversed), the DB session is left in an inconsistent state. No rollback is issued. Subsequent requests in the same session may fail or see dirty data.

**Suggested fix:**  
Add `db.session.rollback()` in the except block for both `delete_sale` and `void_sale`.

---

### H-7 — Default admin password `Admin@12345` hardcoded in `seed.py`
**File:** `seed.py` (line 106)  
**Description:**  
```python
if not admin_password:
    admin_password = "Admin@12345"
```
In development, if `ADMIN_PASSWORD` is not set, the seed script creates an admin user with this well-known default password that is documented in the code. Any developer or attacker who reads the repo can use it to log in.

**Suggested fix:**  
If `ADMIN_PASSWORD` is not set in development, generate a random strong password, print it once to stdout, and refuse to proceed silently with a known default. The code already warns loudly — make it refuse instead.

---

## MEDIUM

### M-1 — `console.log` debug output left in service worker registration
**File:** `smart_mart/templates/store/base_store.html` (lines 695–696)  
**Description:**  
```javascript
.then(reg => console.log('SW registered:', reg.scope))
.catch(err => console.log('SW registration failed:', err));
```
Two `console.log` calls left in production JS. These expose internal scope URLs to anyone with DevTools open. They are also the source of the "extra sign/popup" if Chrome interprets service worker registration errors as a notification (unlikely but possible in some Chrome versions when the SW fails to register on a non-HTTPS connection).

**Suggested fix:**  
Remove or replace with `console.debug()` which is silenced in production builds.

---

### M-2 — Bare `except:` in inventory label generation
**File:** `smart_mart/blueprints/inventory/routes.py` (line 1578)  
**Description:**  
```python
try:    return ImageFont.truetype(_fp, max(6, px))
except: return ImageFont.load_default()
```
A bare `except:` catches `KeyboardInterrupt`, `SystemExit`, and `GeneratorExit` in addition to real errors, which can mask serious problems and interfere with process shutdown.

**Suggested fix:**  
Replace with `except Exception:`.

---

### M-3 — `promotions` routes swallow DB errors without rollback
**File:** `smart_mart/blueprints/promotions/routes.py` (lines 54–56, 77–79, 93–95)  
**Description:**  
All three mutation routes (create, edit, delete) have:
```python
except Exception as e:
    flash(str(e), "danger")
```
No `db.session.rollback()`. If a promotion create or delete fails mid-transaction the session may be in a partial state for the next request.

**Suggested fix:**  
Add `db.session.rollback()` before `flash()` in each except block.

---

### M-4 — `purchases` routes swallow DB errors without rollback
**File:** `smart_mart/blueprints/purchases/routes.py` (lines 133–135, 169–171, 191–193, 248–250)  
**Description:**  
Same pattern as M-3: `except Exception as e: flash(...)` without rollback.

**Suggested fix:**  
Add `db.session.rollback()` in each except block.

---

### M-5 — `_start_debug.py` enables Flask debug mode with `debug=True` bound to all interfaces
**File:** `_start_debug.py` (line 38)  
**Description:**  
```python
app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
```
This file runs Flask's built-in dev server in debug mode (which includes the interactive Werkzeug debugger) bound to all network interfaces. If this is accidentally used on the production LAN or server, any network-reachable client can execute arbitrary Python via the debugger PIN.

**Suggested fix:**  
Add a guard at the top: `assert os.environ.get("FLASK_ENV") != "production"`. Or rename the file to `_start_debug_DO_NOT_USE_IN_PROD.py` to make intent clear.

---

### M-6 — `migrate.py` root script — not idempotent for all columns, uses raw `ALTER TABLE`
**File:** `migrate.py`  
**Description:**  
`migrate.py` uses raw `ALTER TABLE` via `text()` for manual column additions. The application already has an automated schema migration service (`services/schema_migrations.py`) that runs at startup. Having a separate manual migration script creates drift risk: a developer running `migrate.py` may think columns are present when the real migration system has not added them yet.

**Suggested fix:**  
Deprecate `migrate.py`. Add a README note pointing to `schema_migrations.py`. If `migrate.py` must stay, make it call the same migration service instead of duplicating SQL.

---

### M-7 — Payment mode update does not validate sale is not already voided/deleted
**File:** `smart_mart/blueprints/sales/routes.py` (`update_payment_mode`, lines 282–306)  
**Description:**  
The route fetches the sale and allows changing `payment_mode` on any non-deleted sale regardless of its state. There is no check to prevent changing the payment mode of a credit sale that has already been partially or fully collected (`credit_collected=True`), which would make the accounting records inconsistent.

**Suggested fix:**  
Add a guard: if `sale.credit_collected` is `True`, refuse the update and flash a warning.

---

### M-8 — `ai_customer_intelligence.py` product loop calls `db.session.get()` per item
**File:** `smart_mart/services/ai_customer_intelligence.py` (lines 206–210)  
**Description:**  
```python
for pid in bought_ids:
    p = db.session.get(Product, pid)  # 1 query per product
```
This is called from multiple AI endpoints. Even with SQLAlchemy's identity map caching this may miss products not yet in session cache, resulting in per-product queries.

**Suggested fix:**  
Batch-load: `{p.id: p for p in db.session.execute(db.select(Product).where(Product.id.in_(bought_ids))).scalars()}`.

---

## LOW

### L-1 — `console.log` in service worker can indicate broken SW registration on HTTP
**File:** `smart_mart/templates/store/base_store.html` (lines 695–696)  
(Combined with M-1 above — the SW will fail silently on HTTP, and the `console.log` at least surfaces it; however, this is still debug output.)

---

### L-2 — `eSewa` EPAYTEST product code hardcoded as default fallback
**File:** `smart_mart/blueprints/store/routes.py` (line 1268)  
**Description:**  
```python
return _os.environ.get("ESEWA_PRODUCT_CODE", "EPAYTEST")
```
If `ESEWA_PRODUCT_CODE` is not set (e.g., after a fresh deploy where `.env` is missing), the app defaults to the sandbox product code `EPAYTEST` in what may be a production environment. Payments would be silently routed to the sandbox and never received.

**Suggested fix:**  
Return `""` as the default and add a startup check that warns if `ESEWA_PRODUCT_CODE` is unset when `FLASK_ENV=production`.

---

### L-3 — `seed.py` hardcodes default categories and knowledge base articles
**File:** `seed.py`, `smart_mart/services/knowledge_base_seed.py`  
**Description:**  
Both files are one-time setup scripts. They are safe to run repeatedly (idempotency guards exist) but they import `Product`, `Category`, and `User` models directly, creating a coupling that means any model rename silently breaks the seed. There is no test covering the seed path.

**Impact:** Low — only affects fresh installs.

---

### L-4 — `ALLOWED_EXTENSIONS` defined in `inventory/routes.py` then immediately overridden
**File:** `smart_mart/blueprints/inventory/routes.py` (lines 26–28)  
**Description:**  
```python
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}

def _allowed_file(filename: str) -> bool:
    from ...services.image_service import ALLOWED_EXTENSIONS   # shadows module-level set
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
```
The module-level `ALLOWED_EXTENSIONS` is defined but `_allowed_file()` immediately re-imports `ALLOWED_EXTENSIONS` from `image_service`, making the module-level definition dead code.

**Suggested fix:**  
Remove the module-level `ALLOWED_EXTENSIONS = {...}` on line 26 — it is unused.

---

### L-5 — Unused import: `uuid` imported in `inventory/routes.py` but only used inside one nested function
**File:** `smart_mart/blueprints/inventory/routes.py` (line 5)  
**Description:**  
`import uuid` at module level, but `uuid` is only ever used inside `bulk_upload()` as a local `import uuid as _uuid`. The module-level import is redundant but harmless.

---

### L-6 — `ProductInventoryProfile.barcode` conflicts with `Product.barcode`
**File:** `smart_mart/models/operations.py` (line ~65)  
**Description:**  
```python
class ProductInventoryProfile(db.Model):
    barcode = db.Column(db.String(80), nullable=True, unique=True)
```
`Product` already has a `barcode` column. Having a second `barcode` on `ProductInventoryProfile` means there are two places where a product's barcode can be stored, with different unique constraints. If a product's barcode is updated on `Product`, `ProductInventoryProfile.barcode` goes stale. This is a data-consistency risk.

**Suggested fix:**  
Remove `ProductInventoryProfile.barcode` and always use `Product.barcode` as the single source of truth.

---

### L-7 — `bi_dashboard/__init__.py` is a dead empty stub
**File:** `smart_mart/blueprints/bi_dashboard/__init__.py`  
**Description:**  
Contains only `"""Bi Dashboard blueprint."""` — no routes, no imports, no blueprint object. The directory serves no purpose (see H-5).

---

### L-8 — `migrate.py` at root is largely superseded but not marked deprecated
**File:** `migrate.py`  
**Description:**  
`migrate.py` manually adds columns that are now also managed by `services/schema_migrations.py`. Running both could cause confusion. There is no deprecation notice in the file.

---

### L-9 — TODO-adjacent: `THERMAL_PRINTER_NAME` env var not documented in `.env.example` / `.env`
**File:** `smart_mart/services/escpos_printer.py` (line 27)  
**Description:**  
```python
PRINTER_NAME = os.environ.get("THERMAL_PRINTER_NAME", "TVS RP3200 Lite")
```
`THERMAL_PRINTER_NAME` is not listed in `.env`. If the user's printer has a different name (very common — Windows printer names include driver versions and port numbers), printing silently fails with no clear feedback except a cryptic `RuntimeError`.

**Suggested fix:**  
Add `THERMAL_PRINTER_NAME=TVS RP3200 Lite` to `.env` with a comment explaining how to find the correct name.

---

### L-10 — `base_store.html` service worker references `url_for("store.service_worker")` but no such route is audited
**File:** `smart_mart/templates/store/base_store.html` (line 695)  
**Description:**  
```javascript
navigator.serviceWorker.register('{{ url_for("store.service_worker") }}')
```
If `store.service_worker` is not a registered route (or it 404s), the SW registration silently fails and Chrome logs an error. This could be contributing to the popup behavior described in the original issue — a failed SW registration on HTTP can trigger a Chrome "install to home screen" or "add to Chrome" prompt that looks like a popup.

**Suggested fix:**  
Verify `store.service_worker` exists and returns a valid JS file. If it does not, remove the SW registration block entirely or guard it with `{% if store_bp_has_sw %}`.

---

## Summary Table

| # | Severity | File | Issue |
|---|----------|------|-------|
| C-1 | CRITICAL | `templates/sales/create.html` | Barcode `contenteditable` triggers Chrome autofill popup on every scan |
| C-2 | CRITICAL | `.env` | Live API keys, passwords, and secrets in version-controlled file |
| H-1 | HIGH | `serve.py` | Raw Python traceback exposed to browser in 500 responses |
| H-2 | HIGH | `blueprints/sales/routes.py` | `update_payment_mode` — no try/except around `db.session.commit()` |
| H-3 | HIGH | `services/ai_trend_analyzer.py` | N+1 query: 1 DB call per product in `dead_stock_analysis()` |
| H-4 | HIGH | `services/ai_customer_intelligence.py` | N+1 query: 1 DB call per product in upsell recommendation |
| H-5 | HIGH | `blueprints/bi_dashboard/__init__.py` | Dead empty stub — caused documented BuildError 500 on BI pages |
| H-6 | HIGH | `blueprints/sales/routes.py` | `delete_sale`/`void_sale` no rollback on exception |
| H-7 | HIGH | `seed.py` | Hardcoded default admin password `Admin@12345` |
| M-1 | MEDIUM | `templates/store/base_store.html` | `console.log` in SW registration left in production JS |
| M-2 | MEDIUM | `blueprints/inventory/routes.py` | Bare `except:` in label font loading |
| M-3 | MEDIUM | `blueprints/promotions/routes.py` | Missing `db.session.rollback()` in 3 exception handlers |
| M-4 | MEDIUM | `blueprints/purchases/routes.py` | Missing `db.session.rollback()` in 4 exception handlers |
| M-5 | MEDIUM | `_start_debug.py` | Flask debug mode bound to `0.0.0.0` — exposes Werkzeug debugger |
| M-6 | MEDIUM | `migrate.py` | Superseded by `schema_migrations.py`; creates maintenance confusion |
| M-7 | MEDIUM | `blueprints/sales/routes.py` | Payment mode update does not check `credit_collected` guard |
| M-8 | MEDIUM | `services/ai_customer_intelligence.py` | Per-product DB calls in recommendations loop |
| L-1 | LOW | `templates/store/base_store.html` | SW registration `console.log` (duplicate of M-1) |
| L-2 | LOW | `blueprints/store/routes.py` | eSewa `EPAYTEST` default fallback may activate in prod |
| L-3 | LOW | `seed.py` | Seed scripts tightly coupled to models with no test coverage |
| L-4 | LOW | `blueprints/inventory/routes.py` | Dead `ALLOWED_EXTENSIONS` module-level variable |
| L-5 | LOW | `blueprints/inventory/routes.py` | Redundant module-level `import uuid` |
| L-6 | LOW | `models/operations.py` | `ProductInventoryProfile.barcode` duplicates `Product.barcode` |
| L-7 | LOW | `blueprints/bi_dashboard/__init__.py` | Dead empty file |
| L-8 | LOW | `migrate.py` | No deprecation notice; superseded by `schema_migrations.py` |
| L-9 | LOW | `services/escpos_printer.py` | `THERMAL_PRINTER_NAME` not in `.env` — silent printer failures |
| L-10 | LOW | `templates/store/base_store.html` | SW route `store.service_worker` may cause Chrome "add to home" popup |

---

## Positive Findings (What Is Done Well)

- **CSRF protection**: All POST forms include `{{ csrf_token() }}`. AJAX endpoints send it via the `X-CSRFToken` header. The CSRF error handler gives a user-friendly message and redirects appropriately.
- **No SQL injection risk**: All database access uses SQLAlchemy ORM. No raw string-formatted queries found.
- **Row-level locking in `create_sale()`**: The `SELECT ... FOR UPDATE` pattern prevents overselling under concurrent requests.
- **Invoice number collision prevention**: `_lock_invoice_counter()` uses a shop_settings row lock as a sequence semaphore.
- **Security headers**: `X-Content-Type-Options`, `X-Frame-Options`, `Strict-Transport-Security` (prod only), `Content-Security-Policy`, and `Referrer-Policy` are all set via `after_request`.
- **Rate limiting**: Login is rate-limited to 5 attempts/minute via Flask-Limiter with an in-memory fallback.
- **Password strength validation**: `validate_password_strength()` enforces uppercase, digit, and special character requirements.
- **DB indexes**: The `Sale`, `SaleItem`, and `Product` models have comprehensive indexes on all frequently-queried columns.
- **Credit limit enforcement**: `_enforce_credit_limit()` correctly accounts for partial payments before blocking a sale.

---

*Audit completed. All findings are read-only observations. No code was modified.*
