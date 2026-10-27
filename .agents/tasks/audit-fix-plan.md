# Audit Fix Implementation Plan
**Generated:** 2025-07-17  
**Source:** `.agents/tasks/full-audit.md`  
**Target:** Coder agent execution — every item is fully specified with exact current code and replacement code.

---

## Execution Order

Items are ordered so that: (1) no-dep file changes come first, (2) model changes (L-6) come before any service/template changes that touch `profile.barcode`, (3) the dead-code deletion (H-5) comes after we know nothing live depends on the removed folder.

---

## C-1 — Barcode popup: move `#barcodeInput` div outside `<form>` (CRITICAL)

**File:** `smart_mart/templates/sales/create.html`

**Problem:** The `<div id="barcodeInput" contenteditable="true">` lives inside `<form id="saleForm">` (form opens at line 96, closes at line 542). Even though it is a `contenteditable` div rather than an `<input>`, Chrome's autofill/payment heuristics see it as a form element and trigger a credential/payment popup on every barcode scan.

**Fix:** Cut the entire scanner card (the `<div class="card shadow-sm mb-3">` that contains the barcode input and the items table) and move it to **outside the `<form>` tag** — specifically, place it immediately *before* `<form …>` while keeping visual position identical. The JS `handleBarcodeScan()` adds rows directly via `addRow()` without touching the form, so no functional change is required.

**Locate current structure in create.html:**

```
Line ~93:
    </div>                          ← end of customer/offer sections wrapper
                                    ← <-- INSERT barcode card HERE after the move
    <form method="post" … id="saleForm" autocomplete="off">  ← line 96
      <input type="hidden" name="csrf_token" …>
      …
      <div class="card shadow-sm mb-3">           ← line ~405 — Sale Items card (CONTAINS barcode div)
        <div class="card-header …">
          …barcode scanner div here…
        </div>
        <div class="card-body …">items table</div>
        <div class="card-footer …">Add Item / Custom Amount buttons</div>
      </div>
      … (payment, notes, submit)
    </form>                                       ← line 542
```

**Replacement structure:**

```html
    </div>                          ← end of customer/offer sections wrapper

    <!-- MOVED OUTSIDE FORM: barcode scanner card — Chrome does not autofill elements outside a <form> -->
    <div class="card shadow-sm mb-3">
      <div class="card-header d-flex align-items-center gap-2 py-3">
        <i class="bi bi-cart3 text-primary"></i>
        <span class="fw-semibold">Sale Items</span>
        <span id="itemCountBadge" class="badge ms-1" style="background:#94a3b8;color:#fff;font-size:.72rem;">0 items</span>
        <!-- Barcode scanner input — outside <form> so Chrome autofill/payment UI never fires -->
        <div class="ms-auto d-flex align-items-center gap-2">
          <i class="bi bi-upc-scan text-muted"></i>
          <div id="barcodeInput"
               contenteditable="true"
               role="textbox"
               aria-label="Scan barcode or type SKU"
               spellcheck="false"
               data-placeholder="Scan barcode / type SKU…"
               class="form-control form-control-sm"
               style="width:200px; cursor:text; overflow:hidden; white-space:nowrap;"></div>
        </div>
      </div>
      <div class="card-body p-0">
        <div class="table-responsive">
          <table class="table mb-0 align-middle" id="itemsTable">
            <thead>
              <tr>
                <th>Product</th>
                <th style="width:110px;">Qty</th>
                <th style="width:160px;">Unit Price (NPR)</th>
                <th style="width:140px;">Subtotal (NPR)</th>
                <th style="width:50px;"></th>
              </tr>
            </thead>
            <tbody id="itemsBody"></tbody>
          </table>
        </div>
      </div>
      <div class="card-footer bg-white d-flex gap-2">
        <button type="button" class="btn btn-outline-primary btn-sm" id="addItemBtn">
          <i class="bi bi-plus-lg me-1"></i>Add Item
        </button>
        <button type="button" class="btn btn-outline-warning btn-sm" id="addCustomAmtBtn"
                title="For loose/weight items — enter any amount directly">
          <i class="bi bi-calculator me-1"></i>Custom Amount
        </button>
      </div>
    </div>

    <form method="post" action="{{ url_for('sales.create_sale') }}" id="saleForm" autocomplete="off">
```

**What is removed from inside the form:** The entire Sale Items card block (card header with barcode div, card body with items table, card footer with buttons) — currently located between the style block around line 400 and the "ADD OFFER FOR NEXT VISIT" section around line 450. Remove that block from inside the form.

**Verify:** Load the billing page, scan a barcode. Chrome should NOT show an autofill/payment/credential popup. The barcode scan should still work (item added to cart), the items table should still display, and submitting the form should still create the sale correctly.

---

## C-2 — Secrets: `.env` in `.gitignore` + `THERMAL_PRINTER_NAME` in `.env.example`

### C-2a — Verify `.gitignore` already covers `.env`

**File:** `.gitignore`

`.gitignore` already contains:

```
# Environment variables
.env
.env.*
```

**No change needed.** Mark as verified.

### C-2b (= L-9) — Add `THERMAL_PRINTER_NAME` to `.env.example`

**File:** `.env.example`

The file ends with:

```
# ── Log level ────────────────────────────────────────────────────────
LOG_LEVEL=INFO
```

**Add after that block:**

```
# ── Thermal printer (Windows ESC/POS direct printing) ────────────────
# Windows printer name exactly as shown in Settings → Printers.
# To find yours: open PowerShell and run: Get-Printer | Select Name
THERMAL_PRINTER_NAME=TVS RP3200 Lite  # Windows printer name — run: Get-Printer | Select Name
```

**Verify:** Open `.env.example` and confirm the new line is present. No runtime test needed — this is documentation only.

---

## H-1 — `serve.py`: replace raw traceback in 500 response

**File:** `serve.py`

**Current code (lines 33–42):**

```python
@app.errorhandler(Exception)
def _log_exception(e):
    tb = traceback.format_exc()
    # Write to log file
    logging.getLogger(__name__).error("Unhandled exception:\n%s", tb)
    # Also print to console so bat window shows it
    print(tb, file=sys.stderr)
    from flask import request as _r
    return (
        "<pre style='padding:2rem;background:#111;color:#f87171;font-size:13px'>"
        f"500 — Internal Server Error\n\n{tb}</pre>"
    ), 500
```

**Replacement:**

```python
@app.errorhandler(Exception)
def _log_exception(e):
    tb = traceback.format_exc()
    # Write to log file
    logging.getLogger(__name__).error("Unhandled exception:\n%s", tb)
    # Also print to console so bat window shows it
    print(tb, file=sys.stderr)
    return (
        "<div style='padding:2rem;font-family:sans-serif;'>"
        "<h2 style='color:#b91c1c;'>500 — Internal Server Error</h2>"
        "<p>An unexpected error occurred. The server team has been notified.</p>"
        "</div>"
    ), 500
```

**Verify:** Trigger a 500 error (e.g., visit a route that raises an exception in dev). The browser should show the generic message — not a Python traceback. The `logs/waitress-errors.log` file should still contain the full traceback.

---

## H-2 — `update_payment_mode`: add try/except with rollback

**File:** `smart_mart/blueprints/sales/routes.py`

**Current code (the final assignment + commit block, lines ~299–307):**

```python
    sale.payment_mode = new_mode
    sale.payment_method = new_mode
    db.session.commit()
    flash(f"Payment mode updated to {new_mode}.", "success")
    return redirect(url_for("sales.sale_detail", sale_id=sale_id))
```

**Replacement:**

```python
    sale.payment_mode = new_mode
    sale.payment_method = new_mode
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f"Error updating payment mode: {e}", "danger")
        return redirect(url_for("sales.sale_detail", sale_id=sale_id))
    flash(f"Payment mode updated to {new_mode}.", "success")
    return redirect(url_for("sales.sale_detail", sale_id=sale_id))
```

**Verify:** This is a structural change. Confirm the file parses correctly: `python -m py_compile smart_mart/blueprints/sales/routes.py`.

---

## M-7 — `update_payment_mode`: guard against `credit_collected` (MEDIUM, same route as H-2)

**File:** `smart_mart/blueprints/sales/routes.py`

Apply this **in the same edit pass as H-2**, immediately after the valid-mode check and before the assignment block. The full updated block for `update_payment_mode` after both H-2 and M-7 applied:

**After `if new_mode not in valid_modes:` block, insert guard:**

```python
    # Guard: refuse to change payment mode if credit has already been collected
    if getattr(sale, 'credit_collected', False):
        return (
            {"error": "Cannot change payment mode: credit has already been collected."},
            400,
        )
```

Insert this block immediately before `sale.payment_mode = new_mode`.

**Final `update_payment_mode` route body (lines ~280–320) after H-2 + M-7:**

```python
@sales_bp.route("/<int:sale_id>/payment-mode", methods=["POST"])
@login_required
def update_payment_mode(sale_id):
    sale = sales_manager.get_sale(sale_id)

    # Restrict: admins can update any sale; cashiers can only update their own same-day sales.
    if current_user.role != "admin":
        from datetime import date as _date
        if sale.user_id != current_user.id:
            flash("You can only change the payment mode on your own sales.", "danger")
            return redirect(url_for("sales.sale_detail", sale_id=sale_id))
        if sale.sale_date.date() != _date.today():
            flash("You can only change the payment mode on today's sales. Contact an admin for older sales.", "danger")
            return redirect(url_for("sales.sale_detail", sale_id=sale_id))

    new_mode = request.form.get("new_payment_mode", "").strip()
    valid_modes = ["cash", "esewa", "khalti", "fonepay", "qr", "card", "bank", "credit", "split", "other"]
    if new_mode not in valid_modes:
        flash("Invalid payment mode.", "danger")
        return redirect(url_for("sales.sale_detail", sale_id=sale_id))

    # Guard: refuse to change payment mode if credit has already been collected
    if getattr(sale, 'credit_collected', False):
        return (
            {"error": "Cannot change payment mode: credit has already been collected."},
            400,
        )

    sale.payment_mode = new_mode
    sale.payment_method = new_mode
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f"Error updating payment mode: {e}", "danger")
        return redirect(url_for("sales.sale_detail", sale_id=sale_id))
    flash(f"Payment mode updated to {new_mode}.", "success")
    return redirect(url_for("sales.sale_detail", sale_id=sale_id))
```

**Verify:** `python -m py_compile smart_mart/blueprints/sales/routes.py`

---

## H-3 — N+1 in `dead_stock_analysis()`: batch last-sale query

**File:** `smart_mart/services/ai_trend_analyzer.py`

**Current code (lines ~105–122, the per-product loop inside `dead_stock_analysis`):**

```python
    items = []
    for p in products:
        last_sale = db.session.execute(
            db.select(func.max(Sale.sale_date))
            .join(SaleItem, SaleItem.sale_id == Sale.id)
            .where(SaleItem.product_id == p.id)
        ).scalar()
        days_idle = (date.today() - last_sale.date()).days if last_sale and hasattr(last_sale, 'date') else (
            (date.today() - last_sale).days if last_sale and isinstance(last_sale, date) else None
        )
        items.append({
            "id": p.id,
            "name": p.name,
            "sku": p.sku,
            "category": p.category,
            "quantity": p.quantity,
            "cost_price": float(p.cost_price),
            "stock_value": float(p.cost_price) * p.quantity,
            "last_sale_date": str(last_sale.date() if hasattr(last_sale, 'date') else last_sale) if last_sale else None,
            "days_idle": days_idle,
            "action": "clearance_sale" if float(p.cost_price) * p.quantity > 1000 else "remove",
        })
```

**Replacement (single aggregate query, then O(1) dict lookup in loop):**

```python
    # Batch query: get MAX(sale_date) grouped by product_id — single DB round-trip
    last_sale_rows = db.session.execute(
        db.select(
            SaleItem.product_id,
            func.max(Sale.sale_date).label("last_sale"),
        )
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(SaleItem.product_id.in_([p.id for p in products]))
        .group_by(SaleItem.product_id)
    ).all()
    last_sale_map = {row.product_id: row.last_sale for row in last_sale_rows}

    items = []
    for p in products:
        last_sale = last_sale_map.get(p.id)
        days_idle = (date.today() - last_sale.date()).days if last_sale and hasattr(last_sale, 'date') else (
            (date.today() - last_sale).days if last_sale and isinstance(last_sale, date) else None
        )
        items.append({
            "id": p.id,
            "name": p.name,
            "sku": p.sku,
            "category": p.category,
            "quantity": p.quantity,
            "cost_price": float(p.cost_price),
            "stock_value": float(p.cost_price) * p.quantity,
            "last_sale_date": str(last_sale.date() if hasattr(last_sale, 'date') else last_sale) if last_sale else None,
            "days_idle": days_idle,
            "action": "clearance_sale" if float(p.cost_price) * p.quantity > 1000 else "remove",
        })
```

**Verify:** `python -m py_compile smart_mart/services/ai_trend_analyzer.py`

---

## H-4 + M-8 — N+1 in `ai_customer_intelligence.py`: batch product lookups

**File:** `smart_mart/services/ai_customer_intelligence.py`

There are two N+1 sites in `personalized_recommendations()`. Fix both in one edit.

### Site 1 — `top_categories` loop (lines ~206–210, inside `personalized_recommendations`):

**Current code:**

```python
    bought_ids = {p["product_id"] for p in behavior["top_products"]}
    top_categories = set()

    for pid in bought_ids:
        p = db.session.get(Product, pid)
        if p and p.category:
            top_categories.add(p.category)
```

**Replacement:**

```python
    bought_ids = {p["product_id"] for p in behavior["top_products"]}

    # Batch-load all bought products in one query (fixes M-8 N+1)
    bought_products = {
        p.id: p
        for p in db.session.execute(
            db.select(Product).where(Product.id.in_(bought_ids))
        ).scalars().all()
    } if bought_ids else {}

    top_categories = set()
    for pid, p in bought_products.items():
        if p.category:
            top_categories.add(p.category)
```

### Site 2 — `upsell` loop (lines ~223–237, inside `personalized_recommendations`):

**Current code:**

```python
    # Upsell: premium products in same category
    upsell = []
    for pid in list(bought_ids)[:3]:
        p = db.session.get(Product, pid)
        if p:
            premium = db.session.execute(
                db.select(Product)
                .where(Product.category == p.category)
                .where(Product.selling_price > p.selling_price)
                .where(Product.quantity > 0)
                .order_by(Product.selling_price.asc())
                .limit(1)
            ).scalar_one_or_none()
            if premium:
                upsell.append({
                    "current": p.name,
                    "upgrade_to": premium.name,
                    "price_diff": round(float(premium.selling_price) - float(p.selling_price), 2),
                })
```

**Replacement (use `bought_products` dict already built above):**

```python
    # Upsell: premium products in same category (uses bought_products dict — no extra queries)
    upsell = []
    for pid in list(bought_ids)[:3]:
        p = bought_products.get(pid)
        if p:
            premium = db.session.execute(
                db.select(Product)
                .where(Product.category == p.category)
                .where(Product.selling_price > p.selling_price)
                .where(Product.quantity > 0)
                .order_by(Product.selling_price.asc())
                .limit(1)
            ).scalar_one_or_none()
            if premium:
                upsell.append({
                    "current": p.name,
                    "upgrade_to": premium.name,
                    "price_diff": round(float(premium.selling_price) - float(p.selling_price), 2),
                })
```

**Verify:** `python -m py_compile smart_mart/services/ai_customer_intelligence.py`

---

## H-5 — Delete dead `bi_dashboard` blueprint directory

**Files to delete:** `smart_mart/blueprints/bi_dashboard/` (entire directory, contains only `__init__.py` with docstring)

**File to edit:** `smart_mart/app.py`

### Step 1: Remove the dead directory

```
DELETE: d:\Mart Project\smart_mart\blueprints\bi_dashboard\__init__.py
DELETE: d:\Mart Project\smart_mart\blueprints\bi_dashboard\   (directory)
```

### Step 2: Remove dead entry from `app.py`

The `bi_dashboard` folder is **not** the source of `bi_dashboard_bp`. That blueprint actually lives in `.bi.routes.api` (already correctly listed on line 534). The dead entry on line 534 is a duplicate registration of the same blueprint under a second name, which causes a silent `ImportError` warning (or registers the same blueprint twice). The entry on line 533 (`(".bi.routes.api", "bi_bp")`) is what registers the live BI dashboard.

**Current code in `app.py` (lines 531–535):**

```python
        (".blueprints.ai_chat", "ai_chat_bp"),
        (".blueprints.mcp", "mcp_bp"),
        (".bi.routes.api", "bi_bp"),
        (".bi.routes.api", "bi_dashboard_bp"),
    ]
```

**Replacement (remove the duplicate `bi_dashboard_bp` line):**

```python
        (".blueprints.ai_chat", "ai_chat_bp"),
        (".blueprints.mcp", "mcp_bp"),
        (".bi.routes.api", "bi_bp"),
    ]
```

**Verify:** `python -m py_compile smart_mart/app.py` then `python -c "from smart_mart.app import create_app; create_app('development')"` — should complete without `ImportError` or `AlreadyRegistered` errors.

---

## H-6 — `delete_sale` and `void_sale`: add rollback in except blocks

**File:** `smart_mart/blueprints/sales/routes.py`

### `delete_sale` (lines ~593–599):

**Current code:**

```python
    try:
        sales_manager.delete_sale(sale_id)
        flash(f"Sale #{sale_id} has been deleted and stock reversed.", "success")
    except Exception as e:
        flash(f"Error deleting sale: {e}", "danger")
    return redirect(url_for("sales.list_sales"))
```

**Replacement:**

```python
    try:
        sales_manager.delete_sale(sale_id)
        flash(f"Sale #{sale_id} has been deleted and stock reversed.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting sale: {e}", "danger")
    return redirect(url_for("sales.list_sales"))
```

### `void_sale` (lines ~630–636):

**Current code:**

```python
    try:
        sales_manager.delete_sale(sale_id)
        flash(f"Sale #{sale_id} voided — stock has been restored.", "success")
        return redirect(url_for("sales.list_sales"))
    except Exception as e:
        flash(f"Error voiding sale: {e}", "danger")
        return redirect(url_for("sales.sale_detail", sale_id=sale_id))
```

**Replacement:**

```python
    try:
        sales_manager.delete_sale(sale_id)
        flash(f"Sale #{sale_id} voided — stock has been restored.", "success")
        return redirect(url_for("sales.list_sales"))
    except Exception as e:
        db.session.rollback()
        flash(f"Error voiding sale: {e}", "danger")
        return redirect(url_for("sales.sale_detail", sale_id=sale_id))
```

**Verify:** `python -m py_compile smart_mart/blueprints/sales/routes.py`

---

## H-7 — `seed.py`: replace hardcoded `Admin@12345` with `secrets.token_urlsafe`

**File:** `seed.py`

**Current code (inside `ensure_admin`, lines ~99–112):**

```python
    # In development, use a clear default and warn loudly.
    if not admin_password:
        admin_password = "Admin@12345"
        print(
            "WARNING: Using default password Admin@12345. "
            "Set ADMIN_PASSWORD env var before deploying to production."
        )
```

**Replacement:**

```python
    # In development, generate a random password if none is set.
    if not admin_password:
        import secrets as _secrets
        admin_password = _secrets.token_urlsafe(16)
        print(
            f"WARNING: ADMIN_PASSWORD not set. Generated random admin password: {admin_password}\n"
            "         SAVE THIS — it will not be shown again. "
            "Set ADMIN_PASSWORD env var to use a fixed password."
        )
```

**Verify:** `python -m py_compile seed.py` then run `python seed.py` in a test environment without `ADMIN_PASSWORD` set — it should print a random password (not `Admin@12345`).

---

## M-1 + L-10 — `base_store.html`: replace `console.log` with `console.debug` and add try/catch

**File:** `smart_mart/templates/store/base_store.html`

**Current code (lines ~692–700):**

```html
    <script>
      if ('serviceWorker' in navigator) {
        window.addEventListener('load', () => {
          navigator.serviceWorker.register('{{ url_for("store.service_worker") }}')
            .then(reg => console.log('SW registered:', reg.scope))
            .catch(err => console.log('SW registration failed:', err));
        });
      }
    </script>
```

**Replacement (uses `console.debug` + silent catch for L-10):**

```html
    <script>
      if ('serviceWorker' in navigator) {
        window.addEventListener('load', () => {
          try {
            navigator.serviceWorker.register('{{ url_for("store.service_worker") }}')
              .then(reg => console.debug('SW registered:', reg.scope))
              .catch(() => { /* SW registration failed — silent in production */ });
          } catch (_e) { /* SW not supported — ignore */ }
        });
      }
    </script>
```

**Verify:** Open the store page in a browser. Open DevTools Console. No `console.log` output should appear for the service worker. `console.debug` messages are hidden at default DevTools log level.

---

## M-2 — Bare `except:` in inventory label generation

**File:** `smart_mart/blueprints/inventory/routes.py`

**Current code (line ~1577–1578):**

```python
        def _f(px):
            try:    return ImageFont.truetype(_fp, max(6, px))
            except: return ImageFont.load_default()
```

**Replacement:**

```python
        def _f(px):
            try:    return ImageFont.truetype(_fp, max(6, px))
            except Exception: return ImageFont.load_default()
```

**Verify:** `python -m py_compile smart_mart/blueprints/inventory/routes.py`

---

## M-3 — Missing rollback in promotions routes (3 except blocks)

**File:** `smart_mart/blueprints/promotions/routes.py`

### Block 1 — `create_promotion` (lines ~54–56):

**Current:**

```python
            except Exception as e:
                flash(str(e), "danger")
```

**Replacement:**

```python
            except Exception as e:
                db.session.rollback()
                flash(str(e), "danger")
```

### Block 2 — `edit_promotion` (lines ~77–79):

**Current:**

```python
        except Exception as e:
            flash(str(e), "danger")
```

**Replacement:**

```python
        except Exception as e:
            db.session.rollback()
            flash(str(e), "danger")
```

### Block 3 — `delete_promotion` (lines ~93–95):

**Current:**

```python
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("promotions.list_promotions"))
```

**Replacement:**

```python
    except Exception as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("promotions.list_promotions"))
```

**Verify:** `python -m py_compile smart_mart/blueprints/promotions/routes.py`

---

## M-4 — Missing rollback in purchases routes (4 except blocks)

**File:** `smart_mart/blueprints/purchases/routes.py`

The 4 blocks that need `db.session.rollback()` added before the `flash()` call:

### Block 1 — `create_purchase`, `except Exception as e` (line ~133–135):

**Current:**

```python
        except Exception as e:
            flash(f"Error creating purchase: {e}", "danger")
```

**Replacement:**

```python
        except Exception as e:
            db.session.rollback()
            flash(f"Error creating purchase: {e}", "danger")
```

### Block 2 — `create_supplier`, `except Exception as e` (line ~169–171):

**Current:**

```python
        except Exception as e:
            flash(f"Error creating supplier: {e}", "danger")
```

**Replacement:**

```python
        except Exception as e:
            db.session.rollback()
            flash(f"Error creating supplier: {e}", "danger")
```

### Block 3 — `edit_supplier`, `except Exception as e` (line ~191–193):

**Current:**

```python
        except Exception as e:
            flash(str(e), "danger")
```

**Replacement:**

```python
        except Exception as e:
            db.session.rollback()
            flash(str(e), "danger")
```

### Block 4 — `delete_purchase`, `except Exception as e` (line ~248–250):

**Current:**

```python
    except Exception as e:
        flash(f"Error deleting purchase: {e}", "danger")
    return redirect(url_for("purchases.list_purchases"))
```

**Replacement:**

```python
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting purchase: {e}", "danger")
    return redirect(url_for("purchases.list_purchases"))
```

Note: The `bulk_upload` except block at line ~446 (`flash(f"Error saving purchase: {e}", "danger")`) is also present but that path calls `purchase_manager.create_purchase()` — if that fails, the session is still clean from the earlier `db.session.flush()` calls. However, add rollback there too for safety:

### Block 5 — `bulk_upload`, `except Exception as e` saving purchase (line ~446–448):

**Current:**

```python
        except Exception as e:
            flash(f"Error saving purchase: {e}", "danger")
```

**Replacement:**

```python
        except Exception as e:
            db.session.rollback()
            flash(f"Error saving purchase: {e}", "danger")
```

**Verify:** `python -m py_compile smart_mart/blueprints/purchases/routes.py`

---

## M-5 — `_start_debug.py`: add production guard

**File:** `_start_debug.py`

**Current code (top of file, lines 1–4):**

```python
import sys, os, traceback, time

log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "startup_debug.txt")
os.makedirs(os.path.dirname(log_path), exist_ok=True)
```

**Replacement (insert guard after imports, before `log_path`):**

```python
import sys, os, traceback, time

# Safety guard — prevent accidental use in production
if os.environ.get("FLASK_ENV") == "production":
    raise RuntimeError(
        "_start_debug.py must NOT be used in production! "
        "It enables Flask debug mode (Werkzeug interactive debugger) on 0.0.0.0:5000. "
        "Use serve.py (Waitress) for production."
    )

log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "startup_debug.txt")
os.makedirs(os.path.dirname(log_path), exist_ok=True)
```

**Verify:** `python -m py_compile _start_debug.py`

---

## M-6 — `migrate.py`: add deprecation notice

**File:** `migrate.py`

**Current docstring (lines 1–5):**

```python
"""One-time migration — adds missing columns to existing tables.

Usage:
    python migrate.py
"""
```

**Replacement:**

```python
"""
DEPRECATED — Manual migration script.
=======================================================================
This script is superseded by smart_mart/services/schema_migrations.py
which runs automatically at every application startup via create_app().

Running this script manually is UNNECESSARY and may cause confusion:
- schema_migrations.py already handles all column additions idempotently
- Running migrate.py in addition may produce misleading "already exists"
  messages or, in edge cases, duplicate work

If you need to add a new column, add it to schema_migrations.py instead.
=======================================================================

Legacy usage (kept for reference only):
    python migrate.py
"""
```

**Verify:** `python -m py_compile migrate.py`

---

## L-2 — eSewa EPAYTEST: add production warning log

**File:** `smart_mart/blueprints/store/routes.py`

**Current code (lines ~1265–1268):**

```python
def _esewa_product_code() -> str:
    """Return the eSewa product code from env (EPAYTEST for sandbox, live code for production)."""
    import os as _os
    return _os.environ.get("ESEWA_PRODUCT_CODE", "EPAYTEST")
```

**Replacement:**

```python
def _esewa_product_code() -> str:
    """Return the eSewa product code from env (EPAYTEST for sandbox, live code for production)."""
    import os as _os
    import logging as _log
    code = _os.environ.get("ESEWA_PRODUCT_CODE", "EPAYTEST")
    if _os.environ.get("FLASK_ENV") == "production" and code == "EPAYTEST":
        _log.getLogger(__name__).warning(
            "eSewa product code is EPAYTEST in production environment! "
            "Payments will be routed to the eSewa sandbox and never received. "
            "Set ESEWA_PRODUCT_CODE to your live product code."
        )
    return code
```

**Verify:** `python -m py_compile smart_mart/blueprints/store/routes.py`

---

## L-4 — Remove dead `ALLOWED_EXTENSIONS` module-level variable

**File:** `smart_mart/blueprints/inventory/routes.py`

**Current code (lines ~26–28):**

```python
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}


def _allowed_file(filename: str) -> bool:
```

**Replacement (remove the dead module-level set):**

```python
def _allowed_file(filename: str) -> bool:
```

**Verify:** `python -m py_compile smart_mart/blueprints/inventory/routes.py`

---

## L-5 — Remove redundant module-level `import uuid`

**File:** `smart_mart/blueprints/inventory/routes.py`

**Current code (line 5):**

```python
import uuid
```

This `uuid` is only used inside `bulk_upload()` in `purchases/routes.py` as a local `import uuid as _uuid`. In inventory's routes.py, the only use is inside `bulk_upload()` (which already does a local import). Confirm: the module-level `import uuid` on line 5 of `inventory/routes.py` is not referenced anywhere in that file outside a local import.

**Current top of file (lines 1–13):**

```python
"""Inventory blueprint — product CRUD, stock adjustment, and category management."""

from decimal import Decimal
import os
import uuid

from flask import Blueprint, flash, redirect, render_template, request, url_for, current_app, abort
from flask_login import current_user

from ...extensions import db
from ...models.product import Product
from ...models.supplier import Supplier
from ...models.category import Category
```

**Replacement (remove `import uuid`):**

```python
"""Inventory blueprint — product CRUD, stock adjustment, and category management."""

from decimal import Decimal
import os

from flask import Blueprint, flash, redirect, render_template, request, url_for, current_app, abort
from flask_login import current_user

from ...extensions import db
from ...models.product import Product
from ...models.supplier import Supplier
from ...models.category import Category
```

**Note:** Before applying this, search inventory/routes.py for any bare `uuid.` usage (not inside a `import uuid` local block) to confirm there are none. The audit confirmed only `bulk_upload` in purchases routes uses uuid locally.

**Verify:** `python -m py_compile smart_mart/blueprints/inventory/routes.py`

---

## L-6 — Remove `ProductInventoryProfile.barcode` duplicate column

**File:** `smart_mart/models/operations.py`  
**Also touch:** `smart_mart/services/operations_manager.py`, `smart_mart/templates/operations/inventory_tools.html`

### Step 1 — Remove the column from the model

**File:** `smart_mart/models/operations.py`

**Current `ProductInventoryProfile` class (lines ~55–65):**

```python
class ProductInventoryProfile(db.Model):
    __tablename__ = "product_inventory_profiles"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, unique=True)
    barcode = db.Column(db.String(80), nullable=True, unique=True)
    reorder_level = db.Column(db.Integer, nullable=False, default=10)
    shelf_location = db.Column(db.String(80), nullable=True)

    product = db.relationship("Product")
```

**Replacement (remove `barcode` column):**

```python
class ProductInventoryProfile(db.Model):
    __tablename__ = "product_inventory_profiles"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, unique=True)
    reorder_level = db.Column(db.Integer, nullable=False, default=10)
    shelf_location = db.Column(db.String(80), nullable=True)

    product = db.relationship("Product")
```

### Step 2 — Update `update_inventory_profile()` in operations_manager.py

**File:** `smart_mart/services/operations_manager.py`

**Current (lines ~46–52):**

```python
def update_inventory_profile(product_id: int, barcode: str | None, reorder_level: int, shelf_location: str | None = None):
    profile = _profile_for(product_id)
    profile.barcode = (barcode or "").strip() or None
    profile.reorder_level = max(0, int(reorder_level or 0))
    profile.shelf_location = (shelf_location or "").strip() or None
    db.session.commit()
    return profile
```

**Replacement:**

```python
def update_inventory_profile(product_id: int, barcode: str | None, reorder_level: int, shelf_location: str | None = None):
    """Update inventory profile. The `barcode` parameter is kept for API compatibility
    but is now a no-op — barcode is stored on Product.barcode (single source of truth).
    """
    profile = _profile_for(product_id)
    profile.reorder_level = max(0, int(reorder_level or 0))
    profile.shelf_location = (shelf_location or "").strip() or None
    db.session.commit()
    return profile
```

### Step 3 — Update the template to use `product.barcode` instead of `profile.barcode`

**File:** `smart_mart/templates/operations/inventory_tools.html`

**Current line ~19:**

```html
            <td><input name="barcode" class="form-control form-control-sm" value="{{ profile.barcode or '' }}"></td>
```

**Replacement:**

```html
            <td><input name="barcode" class="form-control form-control-sm" value="{{ product.barcode or '' }}"></td>
```

### Step 4 — Update the operations route POST handler to save barcode to `Product`

**File:** `smart_mart/blueprints/operations/routes.py`

The operations route already receives the barcode from the form and passes it to `update_inventory_profile()`. After L-6, that function no longer stores the barcode. We must update the route to set `product.barcode` directly.

**Current code (lines ~150–161):**

```python
    if request.method == "POST":
        try:
            operations_manager.update_inventory_profile(
                product_id=int(request.form.get("product_id", "0")),
                barcode=request.form.get("barcode"),
                reorder_level=int(request.form.get("reorder_level", "10") or 10),
                shelf_location=request.form.get("shelf_location"),
            )
            flash("Inventory profile updated.", "success")
        except ValueError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("operations.inventory_tools"))
```

**Replacement:**

```python
    if request.method == "POST":
        try:
            _product_id = int(request.form.get("product_id", "0"))
            _barcode = (request.form.get("barcode") or "").strip() or None
            # Save barcode directly on Product (single source of truth after L-6)
            _product = db.session.get(Product, _product_id)
            if _product:
                _product.barcode = _barcode
            operations_manager.update_inventory_profile(
                product_id=_product_id,
                barcode=_barcode,
                reorder_level=int(request.form.get("reorder_level", "10") or 10),
                shelf_location=request.form.get("shelf_location"),
            )
            flash("Inventory profile updated.", "success")
        except ValueError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("operations.inventory_tools"))
```

Also confirm `Product` is imported at the top of `operations/routes.py` — grep for `from ...models.product import Product` or equivalent. If not present, add the import.

**Verify:** 
1. `python -m py_compile smart_mart/models/operations.py`
2. `python -m py_compile smart_mart/services/operations_manager.py`
3. Visual check of `inventory_tools.html` — the barcode input should show `product.barcode` value

---

## Checklist Summary

| # | Item | File(s) | Status |
|---|------|---------|--------|
| C-1 | Move barcode div outside `<form>` | `templates/sales/create.html` | ☐ |
| C-2a | Verify `.env` in `.gitignore` | `.gitignore` | ✅ already present |
| C-2b/L-9 | Add `THERMAL_PRINTER_NAME` to `.env.example` | `.env.example` | ☐ |
| H-1 | Replace raw traceback in 500 response | `serve.py` | ☐ |
| H-2 | Add try/except+rollback to `update_payment_mode` | `blueprints/sales/routes.py` | ☐ |
| H-3 | Batch last-sale query in `dead_stock_analysis` | `services/ai_trend_analyzer.py` | ☐ |
| H-4 | Batch product load in `personalized_recommendations` | `services/ai_customer_intelligence.py` | ☐ |
| H-5 | Delete dead `bi_dashboard` dir + remove duplicate import | `blueprints/bi_dashboard/`, `app.py` | ☐ |
| H-6 | Add rollback to `delete_sale` and `void_sale` | `blueprints/sales/routes.py` | ☐ |
| H-7 | Replace `Admin@12345` with `secrets.token_urlsafe` | `seed.py` | ☐ |
| M-1/L-10 | `console.log` → `console.debug` + try/catch SW | `templates/store/base_store.html` | ☐ |
| M-2 | `except:` → `except Exception:` in inventory | `blueprints/inventory/routes.py` | ☐ |
| M-3 | Add rollback to 3 promotions except blocks | `blueprints/promotions/routes.py` | ☐ |
| M-4 | Add rollback to 4+1 purchases except blocks | `blueprints/purchases/routes.py` | ☐ |
| M-5 | Production guard in `_start_debug.py` | `_start_debug.py` | ☐ |
| M-6 | Deprecation notice in `migrate.py` | `migrate.py` | ☐ |
| M-7 | `credit_collected` guard in `update_payment_mode` | `blueprints/sales/routes.py` | ☐ |
| M-8 | Batch product load in upsell (same as H-4) | `services/ai_customer_intelligence.py` | ☐ (combined with H-4) |
| L-2 | Production warning for EPAYTEST default | `blueprints/store/routes.py` | ☐ |
| L-4 | Remove dead `ALLOWED_EXTENSIONS` var | `blueprints/inventory/routes.py` | ☐ |
| L-5 | Remove redundant `import uuid` | `blueprints/inventory/routes.py` | ☐ |
| L-6 | Remove `ProductInventoryProfile.barcode` column | `models/operations.py`, `services/operations_manager.py`, `templates/operations/inventory_tools.html`, `blueprints/operations/routes.py` | ☐ |

---

## Important Notes for Coder Agent

1. **Do NOT modify `.env`** — it contains live credentials.
2. **Do NOT rotate any credentials.**
3. **C-1 is the top priority** — it fixes the original user complaint (popup on barcode scan).
4. **H-2 and M-7 are in the same route** — apply them in a single edit to `update_payment_mode`.
5. **H-4 and M-8 are in the same function** — apply them together in `personalized_recommendations`.
6. **L-6 requires checking the operations blueprint POST handler** — grep `blueprints/operations/routes.py` for `update_inventory_profile` before finalizing.
7. **L-4 and L-5 are in the same file** — apply both in one edit to `inventory/routes.py`.
8. **After all edits**, run `python -m py_compile` on every modified `.py` file to catch syntax errors before committing.
