# Smart Mart POS — Code Quality & Bug Audit
**Date:** 2026-10-26  
**Scope:** Full codebase audit — D:\Mart Project\smart_mart\  
**Triggered by:** Staff reports of bills printing but not appearing in sales records; billing auto-failures

---

## Executive Summary

The original user complaint — *"bills were printed but sales records are missing, billing errors cause auto-failure"* — maps directly to confirmed findings in this audit. The most likely cause is a **poisoned SQLAlchemy session**: an exception raised inside `create_sale` (e.g., a duplicate invoice number, an expired product, a loyalty-wallet DB error) rolls back the transaction correctly, but if any subsequent code path then calls `db.session.commit()` without a clean session, the ORM can silently commit a partial state or raise an `InvalidRequestError` that the `except Exception: rollback; raise` block re-raises to the route — causing a 500 or a flash error *after* the receipt has already been printed. The secondary cause is a sequence of Decimal/int type mismatches that can cause unexpected `TypeError` exceptions mid-transaction.

**Five CRITICAL findings, seven HIGH, six MEDIUM, four LOW.**

---

## CRITICAL

### C-1 · `create_transfer` races on stock check — no row lock before validation
**File:** `smart_mart/services/transfer_manager.py`, lines 17–35  
**Severity:** CRITICAL  

`create_transfer()` does a plain `db.session.get(Product, id)` (no `with_for_update()`) for each item and then checks `product.quantity < qty`. Two concurrent transfers of the same product can both pass this check with the same quantity snapshot, and both then call `db.session.commit()`, leaving the stock column with an incorrect value. `complete_transfer()` (lines 63–84) already uses `with_for_update()` correctly on the same query, but `create_transfer` does not, so the create path has no protection.

**Suggested fix:** Wrap the product fetch in `create_transfer` with `.with_for_update()`, identical to what `complete_transfer` does on line 69.

---

### C-2 · `supplier_return_service.create_supplier_return` — no try/except, no rollback
**File:** `smart_mart/services/supplier_return_service.py`, lines 20–67  
**Severity:** CRITICAL  

The entire function — which flushes a `SupplierReturn` row, loops over items deducting stock and writing `StockMovement` rows, then calls `db.session.commit()` — has zero exception handling. If any iteration raises (e.g., a product no longer exists after the first flush, or an `IntegrityError` on the movement row), SQLAlchemy will be in a dirty state. The caller in `supplier_returns/routes.py` only catches `ValueError`; other exceptions propagate with the session still containing partial uncommitted writes, which means the next request on the same session may commit them unexpectedly, or the session becomes permanently poisoned.

**Suggested fix:** Wrap the body of `create_supplier_return` in `try: ... except: db.session.rollback(); raise` — the same pattern used in `create_sale`, `returns_manager.create_return`, `purchase_manager.create_purchase`, and `transfer_manager.complete_transfer`.

---

### C-3 · `stock_take_service.complete_stock_take` — no lock before stock write
**File:** `smart_mart/services/stock_take_service.py`, lines 42–65  
**Severity:** CRITICAL  

`complete_stock_take` iterates over items and does `product = db.session.get(Product, item.product_id)` with no row lock, then immediately writes `product.quantity = item.counted_qty`. If a sale, purchase, or waste event commits between the stock-take count and this completion call, the stock-take silently overwrites the correct live quantity with the snapshot quantity recorded during the count session, permanently corrupting the ledger.

**Suggested fix:** Replace `db.session.get(Product, item.product_id)` with:
```python
product = db.session.execute(
    db.select(Product).where(Product.id == item.product_id).with_for_update()
).scalar_one_or_none()
```
This is the same pattern used in `complete_transfer` (line 69).

---

### C-4 · `inventory/routes.py` — `variant.quantity = int(...)` truncates decimals
**File:** `smart_mart/blueprints/inventory/routes.py`, line 1851  
**Severity:** CRITICAL  

```python
variant.quantity = int(float(request.form.get("quantity", 0) or 0))
```

`ProductVariant.quantity` is `NUMERIC(10,3)`. Assigning a Python `int` will store only the integer part, silently truncating any fractional stock entered by the admin (e.g., 1.5 kg → stored as 1). This is the exact same class of bug that was just fixed for the main variant quantity column. The billing route (`create_sale`) and the variant stock-check both read this column; a truncated value causes incorrect oversell protection and incorrect invoices.

**Suggested fix:**
```python
variant.quantity = Decimal(str(request.form.get("quantity", 0) or 0))
```

---

### C-5 · `bundles/routes.py` — `int(float(...))` truncates decimal bundle quantities
**File:** `smart_mart/blueprints/bundles/routes.py`, line 91  
**Severity:** CRITICAL  

```python
deduct_qty = int(float(item.quantity) * units)
```

`BundleItem.quantity` is a float/Decimal (e.g., 0.5 for a half-kg ingredient). Converting to `int()` truncates the fractional part, meaning the component product stock deduction is smaller than the actual consumption. Over time this causes stock to drift upward (phantom inventory) with no audit trail, leading to stock-outs that the system doesn't detect until physical count.

**Suggested fix:** Use `Decimal` arithmetic throughout:
```python
from decimal import Decimal
deduct_qty = Decimal(str(item.quantity)) * Decimal(str(units))
_qty_sub(item.component, deduct_qty)
```

---

## HIGH

### H-1 · `supplier_return_service` — `int(qty)` discards fractional stock
**File:** `smart_mart/services/supplier_return_service.py`, line 40  
**Severity:** HIGH  

```python
qty = int(item["quantity"])
```

`SupplierReturnItem.quantity` is `NUMERIC(10,3)`. When a fractional quantity is returned (e.g., 2.5 kg), the integer cast silently truncates it to 2. The stock deduction and the `credit_amount` computation (`qty * unit_cost`) are both based on the truncated integer, leaving 0.5 units of stock that are physically gone unaccounted for, and the supplier credit is understated.

**Suggested fix:** Convert with `Decimal(str(item["quantity"]))` instead of `int(...)`.

---

### H-2 · `transfer_manager.create_transfer` — `int(qty)` discards fractional quantities
**File:** `smart_mart/services/transfer_manager.py`, line 28  
**Severity:** HIGH  

```python
qty = int(item["quantity"])
```

`StockTransferItem.quantity` is defined as `db.Column(db.Integer, ...)`. This is also a schema type bug — the column should be `NUMERIC(10,3)` to match `Product.quantity` and avoid the decimal truncation class. Until it is migrated, the route correctly passes integer quantities from the form, but the mismatch means a future fractional transfer would silently truncate.

**Suggested fix (two parts):**
1. Migrate `stock_transfer_items.quantity` to `NUMERIC(10,3)`.
2. Change `qty = int(item["quantity"])` to `qty = Decimal(str(item["quantity"]))` and update comparisons accordingly.

---

### H-3 · `stock_take/routes.py` — `int(raw)` discards decimal counted quantities
**File:** `smart_mart/blueprints/stock_take/routes.py`, lines 78 and 136  
**Severity:** HIGH  

```python
counts[item.product_id] = int(raw)   # line 78 (form submit)
stock_take_service.update_counts(take_id, {int(product_id): int(counted_qty)})  # line 136 (AJAX)
```

The stock-take stores `system_qty` and `counted_qty` as `NUMERIC(10,3)` (fractional products like grains, liquids). Parsing the input as `int()` discards the decimal, meaning the computed variance is wrong and the correction applied to `products.quantity` is wrong.

**Suggested fix:** Parse as `Decimal(str(raw))` or `float(raw)` in both places, pass floats/Decimals into `update_counts`, and accept `float | Decimal` in the service.

---

### H-4 · `returns/routes.py` — `int(qty)` discards decimal return quantities
**File:** `smart_mart/blueprints/returns/routes.py`, line 88  
**Severity:** HIGH  

```python
parsed_qty = int(qty)
```

`SaleReturnItem.quantity` is `NUMERIC(10,3)`. A customer returning 0.5 kg of a loose product gets `parsed_qty = 0`, which silently passes the `if quantity <= 0: continue` guard and the return is created with zero items, then raises *"Select at least one item quantity to return."* — the user gets an error with no explanation. The stock is never restocked and the refund is never issued.

**Suggested fix:** Parse as `float(qty)` or `Decimal(str(qty))`.

---

### H-5 · `online_orders/routes.py` — stock deduction silently skipped on insufficient stock (TOCTOU race)
**File:** `smart_mart/blueprints/online_orders/routes.py`, lines 190–197  
**Severity:** HIGH  

```python
if product.quantity >= item["quantity"]:
    _qty_sub(product, item["quantity"])
    ...
else:
    db.session.rollback()
    flash(...)
    return render_template(...)
```

The `with_for_update()` lock (added at line 175, which is good) is already acquired, but the stock check and deduction are in an `if/else` inside the for-loop. If stock is insufficient for item N, the route rolls back and re-renders the form — but the `StockMovement` rows and `OnlineOrderItem` rows for items 0…N-1 were already added to the session. This is handled correctly *because* `rollback()` is called. However the `if product.quantity >= item["quantity"]:` check compares a `Decimal` (product.quantity) to a Python `int` (item["quantity"] parsed as `int` on line 122). On PostgreSQL this is fine; on SQLite it can raise `TypeError` before the else-branch is reached, which would leave the session dirty with no explicit rollback from this branch (the outer try/except in the route would catch it, but there is no outer try/except in `create_order`).

**Suggested fix:** Wrap the entire `create_order` POST body in `try: ... except Exception: db.session.rollback(); flash(...); return ...`.

---

### H-6 · `idempotency_key.consume` — TOCTOU race between check-then-insert
**File:** `smart_mart/models/idempotency_key.py`, lines 22–31  
**Severity:** HIGH  

```python
existing = db.session.execute(db.select(cls).where(cls.key == key)).scalar_one_or_none()
if existing:
    return existing
record = cls(key=key, user_id=user_id, endpoint=endpoint)
db.session.add(record)
db.session.flush()
return None
```

Two concurrent POST requests with the same idempotency key will both pass the `if existing:` check (both read `None`), then both call `flush()`. The second flush will raise an `IntegrityError` on the `unique=True` constraint. This error propagates up through `create_sale` (which wraps in try/except rollback/raise) and surfaces as a 500 or a flash error on the second tab. The sale itself is **not** double-committed (the unique index prevents it), but the user sees an error instead of the correct "duplicate detected" message, and the receipt may already have been printed.

**Suggested fix:** Catch `IntegrityError` on the `flush()` and retry the `SELECT` to return the now-existing row, or use `INSERT ... ON CONFLICT DO NOTHING RETURNING *` via raw SQL for atomicity.

---

### H-7 · `waste/routes.py` — no `with_for_update()` on stock deduction
**File:** `smart_mart/blueprints/waste/routes.py`, lines 65–94  
**Severity:** HIGH  

```python
product = db.get_or_404(Product, product_id)   # no lock
stock_quantity = Decimal(str(product.quantity or 0))
if quantity > stock_quantity:
    raise ValueError(...)
product.quantity = new_quantity
```

Two concurrent waste records for the same product can both pass the `if quantity > stock_quantity` check and both apply their deduction, potentially driving the stock below zero. The `CHECK CONSTRAINT ck_product_quantity_non_negative` on the `products` table will catch this on PostgreSQL and raise an `IntegrityError`, but on SQLite the check constraint is not enforced on existing rows, so the value could go negative silently.

**Suggested fix:** Change `db.get_or_404(Product, product_id)` to a `SELECT ... FOR UPDATE` pattern (same as `complete_transfer` line 69).

---

## MEDIUM

### M-1 · `sales_manager.py` — customer upsert exception swallows silently
**File:** `smart_mart/services/sales_manager.py`, ~line 393  
**Severity:** MEDIUM  

```python
except Exception as e:
    logger.warning("Customer upsert failed: %s", e)
```

If `Customer.upsert()` raises an exception (e.g., a unique constraint violation on phone number, which has happened), the sale is committed without a `customer_id`. This means:
- Loyalty points are credited to the wrong wallet (or not at all).
- The credit limit is no longer associated with the customer FK.
- Customer `total_spent` is not updated.

The session state after a failed `db.session.flush()` inside `Customer.upsert` is unpredictable (the flush raises, leaving a partial object in the identity map). The `logger.warning` swallows a potentially real data integrity issue.

**Suggested fix:** Either re-raise the exception (let the whole sale fail cleanly) or explicitly call `db.session.rollback()` and `db.session.begin()` after the catch to reset the session to a clean state before continuing.

---

### M-2 · `sales_manager.py` — audit log commit after main transaction may re-commit dirty state
**File:** `smart_mart/services/sales_manager.py`, ~line 432  
**Severity:** MEDIUM  

```python
db.session.commit()    # main sale commit — good
# ... post-sale code ...
try:
    audit_service.log(...)
    db.session.commit()  # second commit
except Exception as exc:
    logger.warning(...)
```

If the `Customer.upsert` block (M-1) leaves an `IntegrityError`-marked transaction that was never rolled back, this second `db.session.commit()` will raise `InvalidRequestError: Can't reconnect until invalid transaction is rolled back`. This is logged as a warning but the sale record is already committed; the symptom is a 500-like warning in logs after a successful sale, and occasionally the session becomes poisoned for the subsequent request.

**Suggested fix:** Add `db.session.rollback()` in the `except` of the audit log block, or use a separate SQLAlchemy savepoint for the audit log.

---

### M-3 · `bi/services/analytics_service.py` — unbounded full-table Product loads
**File:** `smart_mart/bi/services/analytics_service.py`, lines 108, 357, 418, 639  
**Severity:** MEDIUM  

```python
products = db.session.execute(db.select(Product)).scalars().all()
```

These four call sites load every product row into memory with no `LIMIT`, no filter, and no pagination. On a shop with thousands of products (common in this sector), these block the web worker for potentially seconds. Lines 108 and 639 are inside methods called by the dashboard/BI endpoints which are loaded on every admin page load.

**Suggested fix:** Add appropriate `LIMIT` clauses or streaming pagination. Where aggregate figures are needed (total inventory value, dead stock count), use SQL `SUM`/`COUNT` aggregates instead of loading all rows and summing in Python.

---

### M-4 · `api/routes.py` — `customer_intelligence` N+1 pattern
**File:** `smart_mart/blueprints/api/routes.py`, ~line 248  
**Severity:** MEDIUM  

```python
sales = db.session.execute(
    db.select(Sale).where(func.lower(Sale.customer_name) == c.name.lower())
    ...
).scalars().all()
```

Followed immediately by iterating each sale's `sale_date`. This is acceptable for the data it produces, but the sub-query for `CustomerCreditPayment` inside the same endpoint uses a correlated scalar subquery. When called from the billing POS for every keystroke in the customer field, this executes a non-indexed `func.lower(Sale.customer_name)` full-scan. The `ix_sale_customer_name` index exists but the `lower()` function call prevents index use on SQLite and PostgreSQL without a functional index.

**Suggested fix:** Add a functional index: `CREATE INDEX ix_sale_cname_lower ON sales (lower(customer_name));`

---

### M-5 · `purchases/routes.py` — bulk export loads up to 10,000 rows unbounded
**File:** `smart_mart/blueprints/purchases/routes.py`, line 42  
**Severity:** MEDIUM  

```python
purchases = purchase_manager.list_purchases(filters, per_page=10000)
```

The CSV export fetches 10,000 purchase rows. Each `Purchase` eager-loads `supplier` (via relationship) and the route then accesses `p.supplier.name` and `p.creator.username`. This is an N+1 query: one SELECT for purchases + N SELECTs for suppliers + N SELECTs for creators = up to 20,001 queries for a 10k export, potentially timing out or crashing the worker.

**Suggested fix:** Use `joinedload(Purchase.supplier, Purchase.creator)` in the query, and increase the per_page limit with streaming or chunked response rather than loading everything at once.

---

### M-6 · `supplier_returns/routes.py` — no `with_for_update()` lock in `create_supplier_return`
**File:** `smart_mart/services/supplier_return_service.py`, line 37  
**Severity:** MEDIUM  

```python
product = db.session.get(Product, item["product_id"])   # no lock
if qty > product.quantity:
    raise ValueError(...)
_qty_sub(product, qty)
```

Same race condition class as H-7 (waste). Two concurrent supplier returns on the same product can both pass the quantity check and double-deduct.

**Suggested fix:** Replace with `SELECT ... FOR UPDATE`.

---

## LOW

### L-1 · `auth/routes.py` — password reset link exposed in flash message to any admin
**File:** `smart_mart/blueprints/auth/routes.py`, ~line 104  
**Severity:** LOW  

The reset URL (a bearer token) is shown in a flash message to any authenticated admin:
```python
flash(f"Reset link for '{user.username}' (valid 30 min): {reset_url}", "info")
```

Flash messages are stored in the signed session cookie but also typically rendered in HTML. If the admin's browser session is compromised, or if the admin pastes the URL into a chat or email, the token is exposed. The current implementation logs only the hash of the token (correct), but then renders the full token in the HTML page.

**Suggested fix:** Show only the first 8 characters of the token in the flash and advise the admin to copy from the browser URL bar, or send it via SMS/email only.

---

### L-2 · `store/routes.py` — unauthenticated `contact` form POST modifies data
**File:** `smart_mart/blueprints/store/routes.py`, line 1786  
**Severity:** LOW  

The `/store/contact` route (`POST`) has no rate limiting decorator and no `@login_required`. A bot can POST unlimited messages to this form. The route likely writes to a notification or inquiry table, allowing spam data injection.

**Suggested fix:** Add `@limiter.limit("5/hour")` (consistent with the other public store routes).

---

### L-3 · `stock_take/routes.py` — reference number race in `_next_reference`
**File:** `smart_mart/services/stock_take_service.py`, lines 8–12  
**Severity:** LOW  

```python
max_id = db.session.execute(db.select(db.func.max(StockTake.id))).scalar() or 0
return f"ST-{max_id + 1:05d}-{ts}"
```

Two concurrent stock-take creations can read the same `MAX(id)` and produce the same reference string. The `unique=True` constraint on `stock_takes.reference` will reject the second insert with an `IntegrityError`, which is surfaced to the user as an unexplained 500.

**Suggested fix:** Use a `SEQUENCE` (PostgreSQL) or post-insert update (`take.reference = f"ST-{take.id:05d}"`) after the `flush()` that assigns the real `id`.

---

### L-4 · `supplier_return_service.py` — same reference number race
**File:** `smart_mart/services/supplier_return_service.py`, lines 12–13  
**Severity:** LOW  

Identical issue to L-3. `_next_reference()` reads `MAX(id)` before the row exists; concurrent creates produce the same `SR-XXXXX` reference and the second fails with `IntegrityError`.

**Suggested fix:** Same as L-3 — assign reference after `flush()` using the newly assigned `id`.

---

## Additional Observations (not severity-ranked)

### Performance — dead BI analytics queries
`smart_mart/bi/services/ai_advisor_service.py` line 27 and `analytics_service.py` lines 108/357/639 all do full product table loads inside functions called from the dashboard. For shops with >1,000 products these will be the primary source of slow page loads and occasional Gunicorn/Waitress worker timeouts.

### Dead code — `cash_flow_manager.record_income` is a no-op
`smart_mart/services/cash_flow_manager.py`, line 16:
```python
def record_income(sale) -> None:
    """Income is read directly from the Sale table; no separate income record needed."""
    pass
```
This function is called from `create_sale` and `create_online_order`. It does nothing and never has — but callers pay the import cost and the call overhead. It can be removed, or the call sites can be removed, to reduce confusion.

### `ecommerce_api` — dev mode allows unauthenticated API access
`smart_mart/blueprints/ecommerce_api/routes.py`, line 52:
```python
if not configured:
    if os.environ.get("FLASK_ENV") == "production" or not (current_app.testing or current_app.debug):
        return False
    return True  # dev/test only
```
If `FLASK_ENV` is not set and `DEBUG=False` (a common misconfiguration in staging), this falls through to `True`, allowing unauthenticated access to inventory and order creation APIs. The correct check should require that keys be explicitly configured rather than defaulting to open.

---

## Findings Relevant to User's Original Report

The user's report — *"bill printed but no sale record, billing sometimes auto-fails"* — is most directly explained by:

1. **C-2 / M-1 / M-2 together**: An exception in `Customer.upsert` (e.g., a phone-number uniqueness collision) is swallowed, leaving the SQLAlchemy session in a broken state. The second `db.session.commit()` (for the audit log) then raises `InvalidRequestError`. This propagates back to the route as an unhandled exception, which triggers a rollback of the *main* sale transaction. The receipt has already been rendered (it is generated from the `sale` object in memory, before the final commit), so the cashier sees the print, but the `ROLLBACK` means the database record is gone.

2. **H-6 (idempotency race)**: Under load, two near-simultaneous form submissions (double-click or network retry) hit the idempotency unique constraint. The second raises `IntegrityError`, which propagates through `create_sale`'s `except: rollback; raise`. The receipt for the first submission was already printing when the second raised.

3. **The main sale transaction commits successfully** in the common case, but the post-commit audit/notification code block then calls `db.session.commit()` on a session that may be poisoned from M-1, causing a logged warning that is visually indistinguishable from a complete failure in the logs.

**Immediate remediation priority:**
1. Fix M-1: add `db.session.rollback(); db.session.begin()` (or remove the swallowed exception) in the `Customer.upsert` catch block inside `create_sale`.
2. Fix M-2: add `db.session.rollback()` in the audit-log except block.
3. Fix H-6: handle `IntegrityError` in `IdempotencyKey.consume`.
4. Fix C-2: add try/except/rollback to `create_supplier_return`.

---

## Summary Table

| ID | File | Line(s) | Severity | Category |
|----|------|---------|----------|----------|
| C-1 | services/transfer_manager.py | 17–35 | CRITICAL | Race condition |
| C-2 | services/supplier_return_service.py | 20–67 | CRITICAL | Missing rollback |
| C-3 | services/stock_take_service.py | 42–65 | CRITICAL | Race condition |
| C-4 | blueprints/inventory/routes.py | 1851 | CRITICAL | Type/decimal error |
| C-5 | blueprints/bundles/routes.py | 91 | CRITICAL | Type/decimal error |
| H-1 | services/supplier_return_service.py | 40 | HIGH | Type/decimal error |
| H-2 | services/transfer_manager.py | 28 | HIGH | Type/decimal error |
| H-3 | blueprints/stock_take/routes.py | 78, 136 | HIGH | Type/decimal error |
| H-4 | blueprints/returns/routes.py | 88 | HIGH | Type/decimal error |
| H-5 | blueprints/online_orders/routes.py | 190–197 | HIGH | Missing try/except |
| H-6 | models/idempotency_key.py | 22–31 | HIGH | TOCTOU race |
| H-7 | blueprints/waste/routes.py | 65–94 | HIGH | Race condition |
| M-1 | services/sales_manager.py | ~393 | MEDIUM | Session/transaction |
| M-2 | services/sales_manager.py | ~432 | MEDIUM | Session/transaction |
| M-3 | bi/services/analytics_service.py | 108,357,418,639 | MEDIUM | Performance |
| M-4 | blueprints/api/routes.py | ~248 | MEDIUM | Performance/N+1 |
| M-5 | blueprints/purchases/routes.py | 42 | MEDIUM | Performance/N+1 |
| M-6 | services/supplier_return_service.py | 37 | MEDIUM | Race condition |
| L-1 | blueprints/auth/routes.py | ~104 | LOW | Security |
| L-2 | blueprints/store/routes.py | 1786 | LOW | Security |
| L-3 | services/stock_take_service.py | 8–12 | LOW | Data integrity |
| L-4 | services/supplier_return_service.py | 12–13 | LOW | Data integrity |
