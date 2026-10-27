# Health Audit Fix Plan — Smart Mart POS
**Date:** 2026-10-26  
**Findings to fix:** C-1 through L-4 (22 findings) + ecommerce_api dev-mode auth bypass  
**Source report:** `.agents/tasks/health-audit-2026-10-26.md`

---

## File-grouped change order (minimises merge conflicts)

Group | Files edited
--- | ---
A | `smart_mart/services/transfer_manager.py`
B | `smart_mart/services/supplier_return_service.py`
C | `smart_mart/services/stock_take_service.py`
D | `smart_mart/services/sales_manager.py`
E | `smart_mart/services/purchase_manager.py`
F | `smart_mart/services/schema_migrations.py`
G | `smart_mart/models/idempotency_key.py`
H | `smart_mart/blueprints/inventory/routes.py`
I | `smart_mart/blueprints/bundles/routes.py`
J | `smart_mart/blueprints/stock_take/routes.py`
K | `smart_mart/blueprints/returns/routes.py`
L | `smart_mart/blueprints/online_orders/routes.py`
M | `smart_mart/blueprints/waste/routes.py`
N | `smart_mart/blueprints/purchases/routes.py`
O | `smart_mart/blueprints/auth/routes.py`
P | `smart_mart/blueprints/store/routes.py`
Q | `smart_mart/blueprints/api/routes.py`
R | `smart_mart/blueprints/ecommerce_api/routes.py`
S | `smart_mart/bi/services/analytics_service.py`

---

## Implementation Plan

- [ ] 1. **[C-1] Add `with_for_update()` to product fetch in `create_transfer`**  
      The product is fetched without a row lock, so two concurrent transfers can both pass the stock check.  
      Change line 24 of `create_transfer` to use `.with_for_update()`, matching `complete_transfer` line 69.  
      **File:** `smart_mart/services/transfer_manager.py`  

      **Before (line 24):**
      ```python
              product = db.session.get(Product, int(item["product_id"]))
      ```
      **After:**
      ```python
              product = db.session.execute(
                  db.select(Product).where(Product.id == int(item["product_id"])).with_for_update()
              ).scalar_one_or_none()
      ```
      (The `if not product:` check on the next line already handles `None`; no further changes needed in that block.)  
      **Verify:** `python -m py_compile smart_mart/services/transfer_manager.py`

---

- [ ] 2. **[H-2, part 1] Change `qty = int(...)` to `Decimal` in `create_transfer`**  
      While fixing C-1 the surrounding line also needs the decimal cast.  
      **File:** `smart_mart/services/transfer_manager.py`  

      **Before (line 28):**
      ```python
              qty = int(item["quantity"])
      ```
      **After:**
      ```python
              from decimal import Decimal as _Decimal
              qty = _Decimal(str(item["quantity"]))
      ```
      Move the import to the top of the file (after `from __future__ import annotations`) to keep it clean:  
      Add `from decimal import Decimal` alongside the existing imports at the top of the file.  
      Then the inline use becomes simply `qty = Decimal(str(item["quantity"]))`.  
      **Verify:** `python -m py_compile smart_mart/services/transfer_manager.py`

---

- [ ] 3. **[C-2] Wrap `create_supplier_return` body in `try/except/rollback`**  
      No exception handling; any mid-loop error leaves the session dirty.  
      **File:** `smart_mart/services/supplier_return_service.py`  

      **Before (function body, lines 22–67):**
      ```python
      def create_supplier_return(...) -> SupplierReturn:
          """Create a supplier return and deduct stock immediately."""
          sr = SupplierReturn(
              ...
          )
          db.session.add(sr)
          db.session.flush()

          total_credit = 0.0
          for item in items:
              ...
          sr.credit_amount = total_credit
          db.session.commit()
          return sr
      ```
      **After — wrap the entire body:**
      ```python
      def create_supplier_return(...) -> SupplierReturn:
          """Create a supplier return and deduct stock immediately."""
          try:
              sr = SupplierReturn(
                  ...
              )
              db.session.add(sr)
              db.session.flush()

              total_credit = 0.0
              for item in items:
                  ...
              sr.credit_amount = total_credit
              db.session.commit()
              return sr
          except Exception:
              db.session.rollback()
              raise
      ```
      The `try:` goes immediately after the docstring; `except Exception: db.session.rollback(); raise` goes after the final `db.session.commit()` line.  
      **Verify:** `python -m py_compile smart_mart/services/supplier_return_service.py`

---

- [ ] 4. **[H-1] Change `qty = int(item["quantity"])` to `Decimal` in `create_supplier_return`**  
      Line 40 of `supplier_return_service.py` truncates fractional quantities.  
      **File:** `smart_mart/services/supplier_return_service.py`  

      First, add `from decimal import Decimal` to the top-of-file imports (after `from datetime import datetime, timezone`).  

      **Before (line 40 inside the loop):**
      ```python
              qty = int(item["quantity"])
      ```
      **After:**
      ```python
              qty = Decimal(str(item["quantity"]))
      ```
      The existing `if qty > product.quantity:` comparison and `_qty_sub(product, qty)` both accept `Decimal` without change.  
      **Verify:** `python -m py_compile smart_mart/services/supplier_return_service.py`

---

- [ ] 5. **[M-6] Add `with_for_update()` to product fetch in `create_supplier_return`**  
      Race condition: two concurrent returns on the same product can both pass the stock check.  
      **File:** `smart_mart/services/supplier_return_service.py`  

      **Before (line 37):**
      ```python
              product = db.session.get(Product, item["product_id"])
      ```
      **After:**
      ```python
              product = db.session.execute(
                  db.select(Product).where(Product.id == item["product_id"]).with_for_update()
              ).scalar_one_or_none()
      ```
      **Verify:** `python -m py_compile smart_mart/services/supplier_return_service.py`

---

- [ ] 6. **[L-4] Fix reference-number race in `_next_reference` in `supplier_return_service.py`**  
      Concurrent creations read the same `MAX(id)` before the row exists.  
      **File:** `smart_mart/services/supplier_return_service.py`  

      **Strategy:** Remove `_next_reference()` call from `SupplierReturn(...)` construction and instead assign after `flush()`, using the real database-assigned `id`.  

      **Before (`_next_reference` function + its use in `create_supplier_return`):**
      ```python
      def _next_reference() -> str:
          # Use MAX(id) + 1 so deletions don't produce duplicate reference numbers
          max_id = db.session.execute(db.select(db.func.max(SupplierReturn.id))).scalar() or 0
          return f"SR-{max_id + 1:05d}"


      def create_supplier_return(...) -> SupplierReturn:
          ...
          sr = SupplierReturn(
              reference=_next_reference(),
              ...
          )
          db.session.add(sr)
          db.session.flush()
      ```
      **After:**
      ```python
      def create_supplier_return(...) -> SupplierReturn:
          ...
          sr = SupplierReturn(
              reference="",   # placeholder; assigned after flush gives us the real id
              ...
          )
          db.session.add(sr)
          db.session.flush()
          sr.reference = f"SR-{sr.id:05d}"   # use the assigned PK — race-free
      ```
      Delete the `_next_reference()` function entirely.  
      **Verify:** `python -m py_compile smart_mart/services/supplier_return_service.py`

---

- [ ] 7. **[C-3] Add `with_for_update()` to product fetch in `complete_stock_take`**  
      Line 57: `db.session.get(Product, item.product_id)` has no row lock.  
      **File:** `smart_mart/services/stock_take_service.py`  

      **Before (line 57):**
      ```python
                  product = db.session.get(Product, item.product_id)
      ```
      **After:**
      ```python
                  product = db.session.execute(
                      db.select(Product).where(Product.id == item.product_id).with_for_update()
                  ).scalar_one_or_none()
      ```
      **Verify:** `python -m py_compile smart_mart/services/stock_take_service.py`

---

- [ ] 8. **[L-3] Fix reference-number race in `_next_reference` in `stock_take_service.py`**  
      Same MAX(id) TOCTOU as L-4.  
      **File:** `smart_mart/services/stock_take_service.py`  

      **Strategy:** Assign reference after `flush()` using the real `id`, then delete `_next_reference()`.  

      **Before (`create_stock_take` lines 26–34):**
      ```python
          take = StockTake(
              reference=_next_reference(),
              notes=notes,
              status="in_progress",
              created_by=user_id,
          )
          db.session.add(take)
          db.session.flush()
      ```
      **After:**
      ```python
          take = StockTake(
              reference="",   # placeholder; assigned after flush
              notes=notes,
              status="in_progress",
              created_by=user_id,
          )
          db.session.add(take)
          db.session.flush()
          ts = datetime.now(timezone.utc).strftime("%H%M%S")
          take.reference = f"ST-{take.id:05d}-{ts}"
      ```
      Delete the `_next_reference()` function at lines 11–18.  
      **Verify:** `python -m py_compile smart_mart/services/stock_take_service.py`

---

- [ ] 9. **[M-1] Fix swallowed `Customer.upsert` exception in `sales_manager.py`**  
      Line ~393: `except Exception as e: logger.warning(...)` leaves session dirty.  
      **File:** `smart_mart/services/sales_manager.py`  

      Locate the `except Exception as e: logger.warning("Customer upsert failed: %s", e)` block inside `create_sale`. Add an explicit session rollback and re-begin to reset the ORM state before the sale continues.  

      **Before:**
      ```python
          except Exception as e:
              logger.warning("Customer upsert failed: %s", e)
      ```
      **After:**
      ```python
          except Exception as e:
              logger.warning("Customer upsert failed: %s", e)
              db.session.rollback()
              db.session.begin()
      ```
      **Verify:** `python -m py_compile smart_mart/services/sales_manager.py`

---

- [ ] 10. **[M-2] Add `db.session.rollback()` in audit-log except block in `sales_manager.py`**  
       Line ~432: a second `db.session.commit()` after the main sale commit can raise `InvalidRequestError` if the session is poisoned.  
       **File:** `smart_mart/services/sales_manager.py`  

       Locate the `try: audit_service.log(...) db.session.commit() except Exception as exc: logger.warning(...)` block (after the `except Exception: db.session.rollback(); raise` of the main transaction).  

       **Before:**
       ```python
           except Exception as exc:
               logger.warning("Sale audit logging failed for sale %s: %s", sale.id, exc)
       ```
       **After:**
       ```python
           except Exception as exc:
               logger.warning("Sale audit logging failed for sale %s: %s", sale.id, exc)
               db.session.rollback()
       ```
       **Verify:** `python -m py_compile smart_mart/services/sales_manager.py`

---

- [ ] 11. **[H-2, part 2] Add schema migration: `stock_transfer_items.quantity` → `NUMERIC(10,3)`**  
       The column is `INTEGER`; it must be `NUMERIC(10,3)` to match `Product.quantity`.  
       **File:** `smart_mart/services/schema_migrations.py`  

       Add the following migration tuple **at the end of the `_migration_steps()` list**, immediately before the closing `]`:

       ```python
           (
               "2026_10_26_stock_transfer_items_quantity_numeric",
               "Change stock_transfer_items.quantity from INTEGER to NUMERIC(10,3) "
               "so fractional stock transfers are stored exactly. "
               "Uses same SQLite table-recreate pattern as 2026_10_26_product_variant_quantity_numeric.",
               lambda conn: _migrate_transfer_item_quantity_to_numeric(conn),
           ),
       ```

       Then add the helper function `_migrate_transfer_item_quantity_to_numeric` **immediately below** the existing `_migrate_variant_quantity_to_numeric` function (before `run_pending_migrations`):

       ```python
       def _migrate_transfer_item_quantity_to_numeric(conn) -> None:
           """Recreate stock_transfer_items with quantity as NUMERIC(10,3).

           SQLite does not support ALTER COLUMN TYPE, so we use the standard
           CREATE TABLE + INSERT + DROP + RENAME pattern, identical to
           _migrate_variant_quantity_to_numeric above.
           """
           from sqlalchemy import inspect as _inspect, text as _text

           inspector = _inspect(conn)
           try:
               cols = {c["name"]: c for c in inspector.get_columns("stock_transfer_items")}
               qty_type = str(cols.get("quantity", {}).get("type", "")).upper()
               if "NUMERIC" in qty_type or "DECIMAL" in qty_type or "REAL" in qty_type:
                   return
           except Exception:
               pass

           conn.execute(_text("PRAGMA foreign_keys = OFF"))
           try:
               conn.execute(_text("""
                   CREATE TABLE _stock_transfer_items_new (
                       id          INTEGER PRIMARY KEY,
                       transfer_id INTEGER NOT NULL REFERENCES stock_transfers(id) ON DELETE CASCADE,
                       product_id  INTEGER NOT NULL REFERENCES products(id),
                       quantity    NUMERIC(10,3) NOT NULL DEFAULT 0
                   )
               """))
               conn.execute(_text("""
                   INSERT INTO _stock_transfer_items_new (id, transfer_id, product_id, quantity)
                   SELECT id, transfer_id, product_id, CAST(quantity AS REAL)
                   FROM stock_transfer_items
               """))
               conn.execute(_text("DROP TABLE stock_transfer_items"))
               conn.execute(_text(
                   "ALTER TABLE _stock_transfer_items_new RENAME TO stock_transfer_items"
               ))
               conn.execute(_text(
                   "CREATE INDEX IF NOT EXISTS ix_sti_transfer_id "
                   "ON stock_transfer_items(transfer_id)"
               ))
               conn.execute(_text(
                   "CREATE INDEX IF NOT EXISTS ix_sti_product_id "
                   "ON stock_transfer_items(product_id)"
               ))
           finally:
               conn.execute(_text("PRAGMA foreign_keys = ON"))
       ```
       **Verify:** `python -m py_compile smart_mart/services/schema_migrations.py`

---

- [ ] 12. **[M-4] Add functional index migration: `ix_sale_cname_lower` on `sales(lower(customer_name))`**  
       The `lower()` function call prevents the existing `ix_sale_customer_name` index from being used.  
       **File:** `smart_mart/services/schema_migrations.py`  

       Add the following migration tuple **at the end of `_migration_steps()`**, after the tuple added in step 11:

       ```python
           (
               "2026_10_26_sale_cname_lower_index",
               "Add functional index on sales(lower(customer_name)) so customer_intelligence "
               "queries using func.lower(Sale.customer_name) can use the index on SQLite and "
               "PostgreSQL instead of doing a full table scan.",
               lambda conn: _safe_exec(
                   conn,
                   "CREATE INDEX IF NOT EXISTS ix_sale_cname_lower "
                   "ON sales (lower(customer_name))"
               ),
           ),
       ```
       **Verify:** `python -m py_compile smart_mart/services/schema_migrations.py`

---

- [ ] 13. **[H-6] Handle `IntegrityError` in `IdempotencyKey.consume`**  
       Two concurrent POSTs with the same key both pass the `SELECT` check, then the second `flush()` raises `IntegrityError`.  
       **File:** `smart_mart/models/idempotency_key.py`  

       **Before (lines 22–31):**
       ```python
           @classmethod
           def consume(cls, key: str, user_id: int, endpoint: str) -> "IdempotencyKey | None":
               """Return existing record if key already used, else create a placeholder."""
               existing = db.session.execute(
                   db.select(cls).where(cls.key == key)
               ).scalar_one_or_none()
               if existing:
                   return existing
               record = cls(key=key, user_id=user_id, endpoint=endpoint)
               db.session.add(record)
               db.session.flush()
               return None  # None means "first time — proceed"
       ```
       **After:**
       ```python
           @classmethod
           def consume(cls, key: str, user_id: int, endpoint: str) -> "IdempotencyKey | None":
               """Return existing record if key already used, else create a placeholder.

               Uses a check-then-insert with IntegrityError catch to handle the
               TOCTOU race where two concurrent POSTs with the same key both pass
               the initial SELECT. The unique constraint on `key` guarantees only one
               succeeds; the loser re-queries and returns the winner's row so the
               caller can detect the duplicate cleanly instead of seeing a 500.
               """
               from sqlalchemy.exc import IntegrityError

               existing = db.session.execute(
                   db.select(cls).where(cls.key == key)
               ).scalar_one_or_none()
               if existing:
                   return existing
               record = cls(key=key, user_id=user_id, endpoint=endpoint)
               db.session.add(record)
               try:
                   db.session.flush()
               except IntegrityError:
                   db.session.rollback()
                   # The race loser: the winner's row is now committed — re-fetch it.
                   existing = db.session.execute(
                       db.select(cls).where(cls.key == key)
                   ).scalar_one_or_none()
                   return existing  # non-None signals "duplicate — do not proceed"
               return None  # None means "first time — proceed"
       ```
       **Verify:** `python -m py_compile smart_mart/models/idempotency_key.py`

---

- [ ] 14. **[C-4] Fix `variant.quantity = int(float(...))` in inventory routes**  
       Truncates decimal variant stock.  
       **File:** `smart_mart/blueprints/inventory/routes.py`  

       The file already imports `Decimal` (used elsewhere in the same route). Confirm presence at file top; if absent, add `from decimal import Decimal`.  

       **Before (line 1851):**
       ```python
               variant.quantity = int(float(request.form.get("quantity", 0) or 0))
       ```
       **After:**
       ```python
               variant.quantity = Decimal(str(request.form.get("quantity", 0) or 0))
       ```
       **Verify:** `python -m py_compile smart_mart/blueprints/inventory/routes.py`

---

- [ ] 15. **[C-5] Fix `int(float(...))` bundle quantity truncation in bundles routes**  
       Silently truncates fractional component stock deductions.  
       **File:** `smart_mart/blueprints/bundles/routes.py`  

       Add `from decimal import Decimal` to the top of the file (if not already imported).  

       **Before (line 91):**
       ```python
               deduct_qty = int(float(item.quantity) * units)
       ```
       **After:**
       ```python
               deduct_qty = Decimal(str(item.quantity)) * Decimal(str(units))
       ```
       The immediately following `_qty_sub(item.component, deduct_qty)` and `StockMovement(change_amount=-deduct_qty, ...)` both accept `Decimal` without change. The `summaries.append(f"...: -{deduct_qty}g")` will format fine with `Decimal`.  
       **Verify:** `python -m py_compile smart_mart/blueprints/bundles/routes.py`

---

- [ ] 16. **[H-3] Fix `int(raw)` stock-take count parsing (form submit + AJAX)**  
       Both call sites cast to `int`, discarding fractional counted quantities.  
       **File:** `smart_mart/blueprints/stock_take/routes.py`  

       Add `from decimal import Decimal` to imports.  

       **Before (line 78 — form submit):**
       ```python
                       counts[item.product_id] = int(raw)
       ```
       **After:**
       ```python
                       counts[item.product_id] = Decimal(str(raw))
       ```

       **Before (line 136 — AJAX endpoint):**
       ```python
               stock_take_service.update_counts(take_id, {int(product_id): int(counted_qty)})
       ```
       **After:**
       ```python
               stock_take_service.update_counts(take_id, {int(product_id): Decimal(str(counted_qty))})
       ```
       **Verify:** `python -m py_compile smart_mart/blueprints/stock_take/routes.py`

---

- [ ] 17. **[H-4] Fix `int(qty)` return quantity truncation in returns routes**  
       Returns 0 for fractional quantities, silently failing.  
       **File:** `smart_mart/blueprints/returns/routes.py`  

       Add `from decimal import Decimal` to imports.  

       **Before (line 88):**
       ```python
               parsed_qty = int(qty)
       ```
       **After:**
       ```python
               try:
                   parsed_qty = Decimal(str(qty))
               except Exception:
                   parsed_qty = Decimal("0")
       ```
       Replace the existing `try: parsed_qty = int(qty) / except ValueError: parsed_qty = 0` block with the above.  
       **Verify:** `python -m py_compile smart_mart/blueprints/returns/routes.py`

---

- [ ] 18. **[H-5] Wrap `create_order` POST body in `try/except/rollback` in online_orders routes**  
       Missing outer try/except means `TypeError` from Decimal/int comparison leaves session dirty.  
       **File:** `smart_mart/blueprints/online_orders/routes.py`  

       Locate the POST block for `create_order` (lines 190–197 region). Wrap the entire body (from the product lock `SELECT ... FOR UPDATE` through `db.session.commit()`) in:

       ```python
           try:
               # ... existing POST body code ...
               db.session.commit()
               flash("Order created successfully.", "success")
               return redirect(url_for("online_orders.view", order_id=order.id))
           except Exception as exc:
               db.session.rollback()
               flash(f"Could not create order: {exc}", "danger")
               return render_template("online_orders/create.html", products=products)
       ```
       **Verify:** `python -m py_compile smart_mart/blueprints/online_orders/routes.py`

---

- [ ] 19. **[H-7] Add `with_for_update()` to product fetch in waste routes**  
       `db.get_or_404(Product, product_id)` has no row lock.  
       **File:** `smart_mart/blueprints/waste/routes.py`  

       **Before (line 65 region):**
       ```python
               product = db.get_or_404(Product, product_id)
       ```
       **After:**
       ```python
               product = db.session.execute(
                   db.select(Product).where(Product.id == product_id).with_for_update()
               ).scalar_one_or_none()
               if product is None:
                   raise ValueError(f"Product #{product_id} not found.")
       ```
       Confirm `db` is imported in this file (it is, via `from ...extensions import db`).  
       **Verify:** `python -m py_compile smart_mart/blueprints/waste/routes.py`

---

- [ ] 20. **[M-5] Add `joinedload` for supplier and creator in the purchases CSV export**  
       10 k row export issues N+1 queries (up to 20,001 queries).  
       **File:** `smart_mart/services/purchase_manager.py`  

       Add `from sqlalchemy.orm import joinedload` to the imports.  

       In `list_purchases`, add eager-loading for the two relationships:  

       **Before (line 78):**
       ```python
           stmt = db.select(Purchase).order_by(Purchase.purchase_date.desc())
       ```
       **After:**
       ```python
           from sqlalchemy.orm import joinedload
           stmt = (
               db.select(Purchase)
               .options(joinedload(Purchase.supplier), joinedload(Purchase.creator))
               .order_by(Purchase.purchase_date.desc())
           )
       ```
       **Verify:** `python -m py_compile smart_mart/services/purchase_manager.py`

---

- [ ] 21. **[M-3] Replace full-table Product loads with aggregate queries in `analytics_service.py`**  
       Four call sites load every product row; three are fixable with SQL aggregates.  
       **File:** `smart_mart/bi/services/analytics_service.py`  

       **Site 1 — line 108** (`margin_recommendations`, loads all products to iterate):  
       Keep the full load here because the function must iterate every product to compute per-product recommendations. Add a SQL-level pre-filter to exclude inactive products:  
       ```python
       # Before (line 108):
       products = db.session.execute(db.select(Product)).scalars().all()
       # After:
       products = db.session.execute(
           db.select(Product).where(Product.is_active == True)  # noqa: E712
       ).scalars().all()
       ```

       **Site 2 — line 357** (`inventory_turnover`, loads all products to build `inv_map`):  
       Replace the full product load with an aggregate SQL query that produces only the required `{product_id: inventory_value}` mapping:  
       ```python
       # Before (lines 357-362):
       inv_rows = db.session.execute(db.select(Product)).scalars().all()
       inv_map = {
           p.id: as_decimal(p.quantity or 0) * as_decimal(p.cost_price or 0)
           for p in inv_rows
       }
       # After:
       inv_rows = db.session.execute(
           db.select(
               Product.id,
               (Product.quantity * Product.cost_price).label("inv_value"),
           ).where(Product.cost_price > 0)
       ).all()
       inv_map = {row.id: as_decimal(row.inv_value or 0) for row in inv_rows}
       ```

       **Site 3 — line 418** (`safety_stock_alerts`, loads all products to filter client-side):  
       Replace with a SQL WHERE clause to push the filter to the DB:  
       ```python
       # Before (lines 417-419):
       stmt = db.select(Product).where(Product.quantity >= 0)
       products = db.session.execute(stmt).scalars().all()
       # After (filter also excludes inactive products):
       stmt = db.select(Product).where(Product.is_active == True)  # noqa: E712
       products = db.session.execute(stmt).scalars().all()
       ```
       The loop inside already applies the `qty <= threshold` check per product.

       **Site 4 — line 639** (`inventory_warnings`, loads all products to check dead-stock):  
       Replace with a query filtered to only products with stock > 0 (products with qty == 0 already have no dead-stock risk and the loop skips them with `if qty > 0`):  
       ```python
       # Before (line 639):
       all_products = db.session.execute(db.select(Product)).scalars().all()
       # After:
       all_products = db.session.execute(
           db.select(Product).where(Product.quantity > 0)
       ).scalars().all()
       ```
       **Verify:** `python -m py_compile smart_mart/bi/services/analytics_service.py`

---

- [ ] 22. **[L-1] Truncate reset token in flash message in auth routes**  
       Full bearer token shown in flash HTML; only first 8 characters should be shown.  
       **File:** `smart_mart/blueprints/auth/routes.py`  

       **Before (line ~124):**
       ```python
                   msg = f"Reset link for '{user.username}' (valid 30 min): {reset_url}"
       ```
       **After:**
       ```python
                   token_preview = token[:8] + "…"
                   msg = (
                       f"Reset link for '{user.username}' (valid 30 min) — "
                       f"token starts with: {token_preview}. "
                       "Copy the full link from the browser address bar after visiting the URL."
                   )
       ```
       **Verify:** `python -m py_compile smart_mart/blueprints/auth/routes.py`

---

- [ ] 23. **[L-2] Add rate limiting to `/store/contact` POST route**  
       No rate limit; bots can POST unlimited messages.  
       **File:** `smart_mart/blueprints/store/routes.py`  

       Confirm `limiter` is imported in this file (grep for `from ...extensions import` or `from flask_limiter`). If imported, add the decorator.  

       **Before (line 1783):**
       ```python
       @store_bp.route("/contact", methods=["GET", "POST"])
       def contact():
       ```
       **After:**
       ```python
       @store_bp.route("/contact", methods=["GET", "POST"])
       @limiter.limit("5 per hour", methods=["POST"])
       def contact():
       ```
       **Verify:** `python -m py_compile smart_mart/blueprints/store/routes.py`

---

- [ ] 24. **[ecommerce_api] Fix dev-mode auth bypass — require explicit key configuration**  
       When `FLASK_ENV` is unset and `DEBUG=False`, `_is_authorized()` returns `True`, allowing unauthenticated access.  
       **File:** `smart_mart/blueprints/ecommerce_api/routes.py`  

       **Before (lines 57–63):**
       ```python
       def _is_authorized() -> bool:
           if _is_admin_session():
               return True
           configured = _candidate_keys()
           # Never allow unauthenticated access in production
           if not configured:
               import os
               if os.environ.get("FLASK_ENV") == "production" or not (current_app.testing or current_app.debug):
                   return False
               return True  # dev/test only
           return bool(configured.intersection(_provided_keys()))
       ```
       **After:**
       ```python
       def _is_authorized() -> bool:
           if _is_admin_session():
               return True
           configured = _candidate_keys()
           if not configured:
               # No API keys are configured at all.
               # Only allow fallback-open in an *explicit* testing context; never
               # when DEBUG is simply False or FLASK_ENV is absent/unset.
               # This prevents staging misconfiguration (DEBUG=False, FLASK_ENV unset)
               # from defaulting to open.
               return bool(current_app.testing)
           return bool(configured.intersection(_provided_keys()))
       ```
       **Verify:** `python -m py_compile smart_mart/blueprints/ecommerce_api/routes.py`

---

## Schema migrations summary

Two new entries are appended to `_migration_steps()` in `schema_migrations.py` (Items 11 and 12 above):

| Migration key | What it does |
|---|---|
| `2026_10_26_stock_transfer_items_quantity_numeric` | Recreates `stock_transfer_items` with `quantity NUMERIC(10,3)` using the same SQLite table-recreate pattern as `2026_10_26_product_variant_quantity_numeric`. Idempotent. |
| `2026_10_26_sale_cname_lower_index` | `CREATE INDEX IF NOT EXISTS ix_sale_cname_lower ON sales (lower(customer_name))` — enables the `func.lower(Sale.customer_name)` filter in `customer_intelligence` to use an index. |

---

## Final verification command

After all 24 edits are complete, run:

```powershell
python -m py_compile `
  smart_mart/services/transfer_manager.py `
  smart_mart/services/supplier_return_service.py `
  smart_mart/services/stock_take_service.py `
  smart_mart/services/sales_manager.py `
  smart_mart/services/purchase_manager.py `
  smart_mart/services/schema_migrations.py `
  smart_mart/models/idempotency_key.py `
  smart_mart/blueprints/inventory/routes.py `
  smart_mart/blueprints/bundles/routes.py `
  smart_mart/blueprints/stock_take/routes.py `
  smart_mart/blueprints/returns/routes.py `
  smart_mart/blueprints/online_orders/routes.py `
  smart_mart/blueprints/waste/routes.py `
  smart_mart/blueprints/purchases/routes.py `
  smart_mart/blueprints/auth/routes.py `
  smart_mart/blueprints/store/routes.py `
  smart_mart/blueprints/api/routes.py `
  smart_mart/blueprints/ecommerce_api/routes.py `
  smart_mart/bi/services/analytics_service.py
```

All files must exit with code 0 (no output).

---

## Coverage cross-check

| Audit ID | Finding | Plan item |
|---|---|---|
| C-1 | `create_transfer` — no row lock before validation | Item 1 |
| C-2 | `create_supplier_return` — no try/except, no rollback | Item 3 |
| C-3 | `complete_stock_take` — no lock before stock write | Item 7 |
| C-4 | `inventory/routes.py` — `variant.quantity = int(...)` | Item 14 |
| C-5 | `bundles/routes.py` — `int(float(...))` truncation | Item 15 |
| H-1 | `supplier_return_service` — `int(qty)` discards fractional stock | Item 4 |
| H-2 | `transfer_manager` — `int(qty)` + schema type | Items 2, 11 |
| H-3 | `stock_take/routes.py` — `int(raw)` at lines 78 and 136 | Item 16 |
| H-4 | `returns/routes.py` — `int(qty)` | Item 17 |
| H-5 | `online_orders/routes.py` — missing try/except | Item 18 |
| H-6 | `IdempotencyKey.consume` — TOCTOU race | Item 13 |
| H-7 | `waste/routes.py` — no `with_for_update()` | Item 19 |
| M-1 | `sales_manager.py` — swallowed customer upsert exception | Item 9 |
| M-2 | `sales_manager.py` — audit log re-commit on dirty session | Item 10 |
| M-3 | `analytics_service.py` — unbounded Product loads (4 sites) | Item 21 |
| M-4 | `api/routes.py` — N+1 + missing functional index | Item 12 |
| M-5 | `purchases/routes.py` — N+1 CSV export | Item 20 |
| M-6 | `supplier_return_service.py` — no row lock in create | Item 5 |
| L-1 | `auth/routes.py` — token in flash message | Item 22 |
| L-2 | `store/routes.py` — no rate limit on contact POST | Item 23 |
| L-3 | `stock_take_service.py` — reference number race | Item 8 |
| L-4 | `supplier_return_service.py` — reference number race | Item 6 |
| ecommerce_api | Dev-mode auth bypass | Item 24 |
