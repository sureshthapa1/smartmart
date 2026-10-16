# Smart Mart — Full Application Audit

**Date:** 2026-10-14  
**Scope:** Broad audit across POS/Billing, Inventory, Reports/Dashboard, Auth, Data Integrity, Purchase/Supplier flow, and general code quality. Barcode/label area excluded (already audited separately).

---

## Executive Summary

The codebase is in good overall shape. The critical financial logic (stock locking, double-post prevention, discount validation, credit-limit enforcement, payment-status accuracy) has been hardened with intentional design. However, **five issues could hurt a real store today**:

1. **POS default payment mode is eSewa, not Cash** — cashiers who forget to click will submit every digital payment as eSewa.
2. **Purchase cost overwrites product cost on every restock** — kills historical cost accuracy in profitability reports.
3. **Credit returns do not adjust the sale total** — a refunded credit sale shows the original amount as still outstanding.
4. **Inventory valuation uses live cost price, not historical snapshots** — stale for any product whose cost changed.
5. **In-memory rate limiter is per-process, not per-server** — loses all login-attempt history on restart; no persistence.

---

## Findings

### 1. POS / Billing Screen

#### 1.1 BUG — Default payment mode is eSewa, not Cash  
**Impact: HIGH** | File: `smart_mart/templates/sales/create.html` line ~193

The eSewa radio button has `checked` set:
```html
<input … name="payment_mode" id="pm_esewa" value="esewa" checked>
```
A cashier who doesn't touch the payment section — common during fast counter work — will submit the sale as `payment_mode="esewa"`. The daily cash reconciliation will show zero cash and inflated eSewa, misleading the end-of-day count. Likely a leftover from store-specific configuration that was never reverted to `cash`.

**Fix:** Change the `checked` attribute to the `cash` radio button.

---

#### 1.2 RISK — `_parse_items` accepts zero-price items (price < 0 check only)  
**Impact: MEDIUM** | File: `smart_mart/blueprints/sales/routes.py` lines ~606–633

`_parse_items` continues if `price < 0` but not if `price == 0`. A cashier can scan a product into the cart and sell it for NPR 0 (e.g. accidentally left in the price field), creating a sale that reduces inventory with zero revenue. The `qty <= 0` guard works, but price needs a matching `price <= 0` check for regular products (custom/loose items with price 0 are already excluded with a separate `if price <= 0: continue`).

**Fix:** Change `if qty <= 0 or price < 0:` to `if qty <= 0 or price <= 0:` for the variant and product branches.

---

#### 1.3 IMPROVEMENT — No server-side max-discount-per-product enforcement  
**Impact: MEDIUM** | File: `smart_mart/blueprints/sales/routes.py` lines ~100–112

`Product.max_discount_pct` exists in the model but the discount check in `create_sale` only validates `discount > cart_total`. A cashier with `can_give_discount=True` can apply a 100% discount on any product regardless of the product's `max_discount_pct` cap. The field is silently ignored during billing.

**Fix:** After resolving cart items in `create_sale`, compute the maximum permitted discount from `max_discount_pct` per product and reject if the manual discount exceeds it.

---

#### 1.4 IMPROVEMENT — Change-due calculator visible to all payment modes  
**Impact: LOW** | File: `smart_mart/templates/sales/create.html` lines ~1912–1915

The change-due panel (cash received → change) is toggled visible only for `cash` mode. But the panel itself is not hidden by default on page load — it defaults visible until JavaScript runs. On a slow device or if JS errors out earlier, the panel stays shown even for eSewa/credit payments, which is confusing.

**Fix:** Add `style="display:none"` to the `#changeDuePanel` element in the HTML so it starts hidden regardless of JS state.

---

### 2. Inventory Management

#### 2.1 BUG — `create_purchase` blindly overwrites `product.cost_price` on every restock  
**Impact: HIGH** | File: `smart_mart/services/purchase_manager.py` line ~41

```python
product.cost_price = unit_cost
```

Every purchase unconditionally replaces the product's `cost_price`. This means:
- If a supplier re-delivers at a temporarily higher price (e.g. seasonal spike), all existing stock is now valued at the new cost, distorting inventory valuation.
- The profitability report falls back to `Product.cost_price` when `SaleItem.cost_price` is NULL. Historic sales (before the `cost_price` column was added by migration) are re-valued at the current cost instead of the cost at time of sale, showing incorrect margins.
- Any product sold between purchases will have its margin retroactively recalculated at the new cost.

`SaleItem.cost_price` correctly snapshots cost at time of sale, but `report_engine.py` uses `COALESCE(SaleItem.cost_price, Product.cost_price)` — the fallback means pre-snapshot sales are wrong.

**Fix (two-part):**
1. In `purchase_manager.create_purchase`, only update `product.cost_price` if it is zero/null (i.e. the first purchase), or track a weighted average.
2. In `report_engine.profitability_analysis`, eliminate the `Product.cost_price` fallback entirely. Sales without a cost snapshot should be excluded from margin calculations with a note, not silently mis-valued.

---

#### 2.2 RISK — Inventory valuation report uses live cost price, not historical  
**Impact: MEDIUM** | File: `smart_mart/services/report_engine.py` lines ~128–136

`inventory_valuation()` multiplies `Product.cost_price * Product.quantity`. Since finding 2.1 above means `cost_price` is always the most recent purchase price, the valuation report will jump significantly whenever a supplier charges more on the latest delivery — even if most of the stock on the shelf was bought cheaper.

**Fix:** Consider adding a weighted average cost (WAC) or last-purchase-cost field to Product for valuation. For now, document the limitation visibly in the UI.

---

#### 2.3 RISK — Deleting a category leaves Product.category as a dangling string  
**Impact: MEDIUM** | File: `smart_mart/blueprints/inventory/routes.py` lines ~277–284

`delete_category` deletes the `Category` row with no cascade. `Product.category` is a plain `VARCHAR` string, not a FK to `categories.id`, so products referencing that category name continue to have the old string but no corresponding category. The category filter in reports will silently work (matching by string), but the UI shows "Unknown" or "Uncategorized" incorrectly.

**Fix:** Before deleting, either re-assign products in that category to a default, or raise an error if products exist.

---

#### 2.4 RISK — Bulk product upload (inventory) sets `selling_price = cost` if selling price not provided  
**Impact: MEDIUM** | File: `smart_mart/blueprints/inventory/routes.py` lines ~252–254

```python
sell = float(_clean_num(sell_raw)) if sell_raw else cost
```

If a CSV row omits the selling price column, the system silently sets selling price equal to cost price, yielding 0% margin. A staff member doing a bulk upload of supplier invoices (which typically only have cost) will create dozens of products selling at cost with no warning.

**Fix:** Emit a warning flash for each row where selling price was defaulted to cost price, so the user knows to review those products.

---

#### 2.5 IMPROVEMENT — Low-stock threshold logic inconsistency  
**Impact: LOW** | File: `smart_mart/blueprints/inventory/routes.py` line ~70, `smart_mart/blueprints/dashboard/routes.py` line ~222

The inventory list's low-stock filter uses `Product.quantity <= Product.low_stock_threshold` while the dashboard's `reorder_alerts_count` uses `Product.quantity <= Product.reorder_point`. These are two different fields with different semantics, and the dashboard alert badge count can differ from the inventory list's low-stock count, causing confusion.

**Fix:** Decide on one field for "needs reorder" alerts and use it consistently. The `reorder_point` field is better named; `low_stock_threshold` could be deprecated.

---

### 3. Reports & Dashboard

#### 3.1 BUG — Credit/Udharo: returns do not reduce the outstanding balance  
**Impact: HIGH** | File: `smart_mart/services/returns_manager.py`, `smart_mart/blueprints/reports/routes.py` lines ~599–620

`create_return` correctly restocks inventory and records a refund but does **not** reduce `Sale.total_amount` and does **not** call `record_credit_payment` or create a `CustomerCreditPayment` row for the refunded amount. So for a credit sale where the customer returns goods:
- The outstanding balance on the credit ledger still shows the full original amount.
- The customer still needs to "pay" the full amount to get `credit_collected = True`.
- The operations credits dashboard and `credit_udharo` report both show inflated outstanding debt.

**Fix:** In `returns_manager.create_return`, if the parent sale is a credit sale, create a `CustomerCreditPayment` record equal to the refund amount (with `payment_mode="return"`), which will reduce the outstanding balance and correctly flip `credit_collected` when the balance reaches zero.

---

#### 3.2 RISK — Dashboard "today_profit" excludes non-COGS expenses  
**Impact: MEDIUM** | File: `smart_mart/blueprints/dashboard/routes.py` lines ~162–165

```python
stats["today_profit"] = stats["today_sales_amount"] - stats["today_cogs"]
```

This uses `revenue - COGS` but ignores today's expenses (rent, utilities, wages). The `monthly_profit` figure correctly uses `cash_flow_manager.profit_loss()` which deducts expenses, but the prominently-displayed "today's profit" card is inflated by today's operating costs. An owner looking at it each morning gets an optimistic number.

**Fix:** Either rename the card "Today's Gross Profit" in the UI so the distinction is clear, or compute net profit the same way `monthly_profit` does.

---

#### 3.3 RISK — Credit report outstanding amounts ignore partial payments  
**Impact: MEDIUM** | File: `smart_mart/blueprints/reports/routes.py` lines ~604–620

The `credit_udharo` report summary calculates `pending_amount` as:
```python
sum(float(s.total_amount) for s in all_credit if not s.credit_collected)
```
This does not subtract partial payments already recorded in `CustomerCreditPayment`. A sale marked as uncollected (partial payment has been made, balance > 0) counts its full original amount as outstanding. The actual outstanding is less — `sales_manager._customer_outstanding_credit` does the correct calculation but it is not used here.

**Fix:** Replace the inline sum with a query that joins `CustomerCreditPayment` and subtracts paid amounts, or call `_customer_outstanding_credit` per customer.

---

#### 3.4 IMPROVEMENT — Stock analysis report uses stock movements but ignores variant stock  
**Impact: MEDIUM** | File: `smart_mart/services/report_engine.py` (opening/closing stock)

Stock movements are logged at the parent product level even for variant sales (with the variant noted in the `note` field). If a product has variants, the `opening_closing_stock` report aggregates movement rows by `product_id`, which may double-count: movement volume for a variant sale is already reflected in the parent movement row AND in the variant quantity change. This can show more units moved than the parent product's own stock changed.

**Fix:** Filter `StockMovement` by `change_type NOT IN ('sale', 'sale_return')` when the product has active variants, and derive those figures from `ProductVariant.quantity` changes instead.

---

### 4. User / Auth System

#### 4.1 RISK — In-memory rate limiter resets on every server restart  
**Impact: MEDIUM** | File: `smart_mart/services/authenticator.py` lines ~15–20

`_login_attempts` is a Python dictionary in memory. On Windows with Waitress (as this store appears to use: `waitress-errors.log` present), a server restart (which can happen after any crash) wipes all tracked attempts. An attacker who triggers a restart (or simply waits for the daily restart) gets a fresh `_MAX_ATTEMPTS` allowance. The Flask-Limiter decorator on the `/auth/login` route (`@limiter.limit("5 per minute")`) provides a backup but it also defaults to in-memory storage unless `RATELIMIT_STORAGE_URI` is configured.

**Fix:** Check `RATELIMIT_STORAGE_URI` in config — if not set to Redis or a DB backend, the Flask-Limiter is also in-memory. For a single-machine deployment this is acceptable but should be documented. Consider persisting login attempts to `LoginAttempt` DB table (which already exists) and querying it in `is_rate_limited`.

---

#### 4.2 IMPROVEMENT — Password reset link exposed in logs  
**Impact: MEDIUM** | File: `smart_mart/blueprints/auth/routes.py` lines ~82–90

```python
current_app.logger.warning(
    "PASSWORD RESET requested for user '%s' … Reset URL (valid 30 min): %s",
    user.username, user.id, reset_url,
)
```

The full reset URL (which is a bearer token for account takeover) is written to the application log at `WARNING` level. Anyone with read access to `smart_mart.log` (which is in the project root, potentially accessible via a misconfigured web server) can take over any account. The comment says "always log as security audit trail" but a URL vs. a hash of the token would serve the same audit purpose without the exposure.

**Fix:** Log `user.username` and `user.id` only. If a URL must be in the log, log the token's SHA-256 hash so it proves the token was generated without being reusable from the log.

---

#### 4.3 RISK — Staff can view all customers' statements if `can_view_customer_statement` is granted  
**Impact: LOW** | File: `smart_mart/blueprints/sales/routes.py` — `customer_statement` route

The customer statement page (`/sales/customer-statement`) searches by name but returns the full purchase history, credit balance, loyalty points, top products, and contact information of any customer by name. There is no customer ownership model — any staff member with `can_view_customer_statement` can look up any customer's complete financial history.

This is acceptable for a small retail store but should be noted as a privacy risk if this is a multi-staff shop serving recurring credit customers.

---

### 5. Data Integrity

#### 5.1 RISK — `Customer.total_spent` can drift from actual sale totals  
**Impact: MEDIUM** | File: `smart_mart/services/sales_manager.py` lines ~304–316

`create_sale` adds `total_amount` to `Customer.total_spent`. But `delete_sale` and `void_sale` do not subtract from `Customer.total_spent`. Similarly, `create_return` does not reduce it. Over time, voided/returned sales inflate `Customer.total_spent`, causing incorrect loyalty tier upgrades and potentially triggering offers the customer doesn't merit.

**Fix:** In `delete_sale`, subtract `sale.total_amount` from `sale.customer.total_spent` before deletion. In `create_return`, subtract `refund_amount` proportionally.

---

#### 5.2 RISK — `SaleItem.product_id` is nullable but no FK constraint on variants  
**Impact: LOW** | File: `smart_mart/models/sale.py` lines ~65–72

`SaleItem.variant_id` has `ondelete="SET NULL"`. If a `ProductVariant` is hard-deleted (not just deactivated), all sale items referencing it silently lose their variant link. The void/return logic handles this case gracefully (logs a warning, skips stock movement), but historical reports that join `sale_items → product_variants` will silently drop those line items from statistics.

**Fix:** Add a soft-delete (`is_active`) flag to `ProductVariant` instead of hard deletes, mirroring the `Product.is_active` pattern.

---

#### 5.3 RISK — `schema_migrations.py` uses `_safe_exec` that swallows all DDL errors  
**Impact: MEDIUM** | File: `smart_mart/services/schema_migrations.py`

```python
def _safe_exec(conn, sql: str) -> None:
    try:
        conn.execute(text(sql))
    except Exception:
        pass
```

Every migration that uses `_safe_exec` (indexes, column additions) silently ignores failures. If a migration step fails due to a genuine schema conflict (column with incompatible type already exists, table locked, etc.) the migration is recorded as successful and the column is never actually created. The next request that accesses the missing column crashes with an unhandled `OperationalError`.

This is a known trade-off for idempotent migrations on SQLite, but it means migration failures are invisible.

**Fix:** Log the exception at `WARNING` level even when swallowing it, so failures appear in `smart_mart.log` and can be diagnosed.

---

### 6. Purchase / Supplier Flow

#### 6.1 BUG — Bulk purchase upload creates products with `selling_price = cost_price`  
**Impact: HIGH** | File: `smart_mart/blueprints/purchases/routes.py` lines ~376–396

When a product is not found by SKU or name during a bulk purchase upload, the system auto-creates it with:
```python
"cost_price": cost,
"selling_price": cost,
```
This silently creates new products at zero margin. In a store that regularly receives supplier invoices and uploads them as bulk purchases, this means new products go live immediately at cost price with no markup — they're immediately visible on the POS and can be sold before the admin even notices.

**Fix:** Auto-created products should be flagged `is_active=False` by default, requiring explicit activation with selling price review. Add a flash warning listing auto-created product names.

---

#### 6.2 IMPROVEMENT — No duplicate purchase guard  
**Impact: MEDIUM** | File: `smart_mart/blueprints/purchases/routes.py`, `smart_mart/services/purchase_manager.py`

The purchase creation form has no idempotency key (unlike the sale form). Submitting the form twice (network retry, double-click) creates two identical purchase records and doubles the stock quantity. This is particularly risky for large bulk uploads.

**Fix:** Add an idempotency key to the purchase creation form using the same `IdempotencyKey.consume()` pattern already used in `sales.create_sale`.

---

#### 6.3 IMPROVEMENT — Supplier payment does not validate against total purchase cost  
**Impact: MEDIUM** | File: `smart_mart/services/operations_manager.py` — `record_supplier_payment`

Supplier payments accept any amount without checking whether the total paid already exceeds the purchase total. Unlike `record_credit_payment` (which enforces `amount <= outstanding`), over-payments to suppliers are silently recorded. The supplier balance screen will show a negative balance (overpayment) but there's no guard to prevent it.

**Fix:** Mirror the credit payment guard: compute total paid for the purchase and reject payments that would exceed the purchase total.

---

### 7. General Code Quality

#### 7.1 RISK — `purchase_manager.list_purchases` used for CSV export with `per_page=10000`  
**Impact: MEDIUM** | File: `smart_mart/blueprints/purchases/routes.py` lines ~38–42

```python
purchases = purchase_manager.list_purchases(filters, per_page=10000)
```

The CSV export loads up to 10,000 purchase records into memory. Each `Purchase` eager-loads its `PurchaseItem` rows via the ORM relationship. A shop with 3 years of purchase history and 20 items per purchase could load 200,000 PurchaseItem rows into memory in one request, causing a significant memory spike.

**Fix:** Use a streaming cursor / `yield_per()` approach for exports, or at minimum cap at a lower bound and warn if the export is truncated.

---

#### 7.2 RISK — `list_categories` / `category_detail` have N+1 risk on low-inventory shops  
**Impact: LOW** | File: `smart_mart/blueprints/inventory/routes.py` lines ~182–242

The category list and detail pages make several aggregation queries. The implementation uses a single aggregation query each (`stats_map`, `rev_map`) which is correct for most data, but the `enriched` list is built by iterating over `products` and doing a per-product Python lookup in `sales_map`. This is an in-Python join and is fine as long as the product list fits in memory. For stores with 1,000+ products in a single category, this will be slow.

**Fix:** Move the enrichment logic into a single SQL query with GROUP BY instead of the Python dict lookup.

---

#### 7.3 IMPROVEMENT — `customer_statement` route counts partially-paid credit as collected  
**Impact: MEDIUM** | File: `smart_mart/blueprints/sales/routes.py` lines ~365–366

```python
collected = sum(float(s.total_amount) for s in credit_sales if s.credit_collected)
```

This counts the full `total_amount` for a collected sale, even if the collection came from multiple partial payments over time. More critically, the `outstanding` calculation does not subtract partial payments for uncollected sales (mirror of finding 3.3 above). A customer who has made two partial payments against a large credit balance looks worse on their statement than they are.

**Fix:** Join `CustomerCreditPayment` to compute actual paid amounts per sale for the statement.

---

#### 7.4 RISK — Unhandled exception can expose internal DB errors to users  
**Impact: MEDIUM** | File: `smart_mart/blueprints/purchases/routes.py` lines ~123–125, and multiple other blueprints

Several POST handlers catch `Exception as e` and `flash(f"Error creating X: {e}", "danger")`. Raw SQLAlchemy exceptions (e.g. `UNIQUE constraint failed: products.sku`) are surfaced directly to users. This exposes schema information and can confuse non-technical staff ("IntegrityError: UNIQUE constraint failed: products.sku" instead of "A product with this SKU already exists").

**Fix:** In each service function, catch `IntegrityError` and translate it to a user-friendly `ValueError` before re-raising. The product creation route does this already for SKU conflicts (`create_product` raises `ValueError`) but `update_product` in the same file also wraps with `ValueError` — extend this pattern to purchase creation and supplier creation.

---

## Summary Table

| # | Area | Type | Impact | File (approx. line) |
|---|------|------|--------|----------------------|
| 1.1 | POS | BUG | HIGH | `templates/sales/create.html:193` |
| 1.2 | POS | RISK | MEDIUM | `blueprints/sales/routes.py:606` |
| 1.3 | POS | IMPROVEMENT | MEDIUM | `blueprints/sales/routes.py:100` |
| 1.4 | POS | IMPROVEMENT | LOW | `templates/sales/create.html:1912` |
| 2.1 | Inventory | BUG | HIGH | `services/purchase_manager.py:41` |
| 2.2 | Inventory | RISK | MEDIUM | `services/report_engine.py:128` |
| 2.3 | Inventory | RISK | MEDIUM | `blueprints/inventory/routes.py:277` |
| 2.4 | Inventory | RISK | MEDIUM | `blueprints/inventory/routes.py:252` |
| 2.5 | Inventory | IMPROVEMENT | LOW | `blueprints/inventory/routes.py:70` |
| 3.1 | Reports | BUG | HIGH | `services/returns_manager.py:~110` |
| 3.2 | Dashboard | RISK | MEDIUM | `blueprints/dashboard/routes.py:162` |
| 3.3 | Reports | RISK | MEDIUM | `blueprints/reports/routes.py:604` |
| 3.4 | Reports | IMPROVEMENT | MEDIUM | `services/report_engine.py` |
| 4.1 | Auth | RISK | MEDIUM | `services/authenticator.py:15` |
| 4.2 | Auth | IMPROVEMENT | MEDIUM | `blueprints/auth/routes.py:82` |
| 4.3 | Auth | RISK | LOW | `blueprints/sales/routes.py` (customer_statement) |
| 5.1 | Data Integrity | RISK | MEDIUM | `services/sales_manager.py:304` |
| 5.2 | Data Integrity | RISK | LOW | `models/sale.py:65` |
| 5.3 | Data Integrity | RISK | MEDIUM | `services/schema_migrations.py` |
| 6.1 | Purchases | BUG | HIGH | `blueprints/purchases/routes.py:376` |
| 6.2 | Purchases | IMPROVEMENT | MEDIUM | `blueprints/purchases/routes.py` |
| 6.3 | Purchases | IMPROVEMENT | MEDIUM | `services/operations_manager.py` |
| 7.1 | Code Quality | RISK | MEDIUM | `blueprints/purchases/routes.py:38` |
| 7.2 | Code Quality | RISK | LOW | `blueprints/inventory/routes.py:182` |
| 7.3 | Code Quality | IMPROVEMENT | MEDIUM | `blueprints/sales/routes.py:365` |
| 7.4 | Code Quality | RISK | MEDIUM | `blueprints/purchases/routes.py:123` |

---

## Priority Fix Order (Recommended)

**Fix immediately (blocking real store operations):**
1. **1.1** — Change POS default to `cash` (2-line fix, zero risk).
2. **3.1** — Credit returns must reduce outstanding balance (medium complexity).
3. **6.1** — Bulk purchase auto-creates live products at zero margin (add `is_active=False`).
4. **2.1** — Stop overwriting `cost_price` on every purchase.

**Fix this week:**
5. **5.1** — Keep `Customer.total_spent` in sync with voids/returns.
6. **3.3** — Fix credit report pending_amount to subtract partial payments.
7. **6.2** — Add idempotency key to purchase form.
8. **4.2** — Don't log full reset URLs.
9. **7.4** — Translate DB exceptions to user-friendly messages.

**Fix this sprint:**
10. **2.3**, **2.4**, **3.2**, **4.1**, **5.3**, **6.3**, **7.1** — remaining medium-impact items.
