# Barcode Scanning & Billing — Readiness Report

**Verdict: Mostly ready. The end-to-end flow exists but has 3 gaps that will break scanning for real EAN/UPC barcodes in production.**

---

## READY — Things That Already Work

### 1. Product model has a `barcode` field
- `smart_mart/models/product.py` — `barcode = db.Column(db.String(80), nullable=True)` (line 22)
- Documented as "EAN-13/UPC manufacturer barcode — separate from SKU"
- Schema migration `2026_06_01_product_barcode` adds the column safely via `_safe_add_column`

### 2. Variant model also has a `barcode` field
- `smart_mart/models/product_variant.py` — `barcode = db.Column(db.String(80), nullable=True, unique=True)`
- Has a `UNIQUE` constraint — prevents two variants sharing one barcode
- Variants have their own independent barcodes, separate from the parent product

### 3. Barcode input exists on the POS billing screen
- `smart_mart/templates/sales/create.html` (line ~384):
  ```html
  <input type="text" id="barcodeInput" ... placeholder="Scan barcode / type SKU…">
  ```
- Positioned in the card header of the Sale Items section, visually correct placement

### 4. JavaScript barcode scanner logic is implemented
- `handleBarcodeScan(sku)` function (line ~1422) performs a 4-tier lookup:
  1. Exact barcode match (`p.barcode === q`)
  2. Exact SKU match (case-insensitive)
  3. Exact product name match
  4. Partial product name match
- On match: adds new cart row OR increments qty on existing row (smart dedup)
- On miss: shows a red toast error `❌ SKU/Barcode not found: <value>` and stays focused — correct POS UX
- Both Enter-key (scanner hardware) and 300ms auto-timeout (scanners that don't send Enter) are handled
- F2 keyboard shortcut focuses the barcode input

### 5. All products AND variants are embedded in the page JS on load
- `smart_mart/blueprints/sales/routes.py` (lines 85-98): loads all active, in-stock products and variants
- Both are serialized into the `PRODUCTS` JS array in the template with `id`, `name`, `sku`, `barcode`, `price`, `stock`
- Variant barcodes are included: `barcode: {{ (v.barcode or '') | tojson }}`

### 6. Barcode entry in product create/edit form
- `smart_mart/templates/inventory/form.html`: dedicated "Barcode (EAN/UPC)" field, separate from SKU
- Help text correctly says "Manufacturer barcode — separate from SKU"
- Barcode is saved via `inventory_manager.create_product()` and `update_product()`

### 7. Barcode entry in variant form
- `smart_mart/templates/inventory/variant_form.html`: "Barcode" field next to SKU
- Barcode is saved when creating/editing a variant

### 8. Barcode label printing works
- `/inventory/labels/print` — generates Code128 barcode PNG labels (PDF and direct sticker printer)
- Falls back to SKU if no barcode: `barcode: {{ (p.barcode or p.sku)|tojson }}`
- `/inventory/labels/barcode?value=<sku>` — returns base64 barcode PNG on demand

### 9. Bulk auto-generate barcodes from SKU
- `POST /inventory/autofill-barcodes` — sets `barcode = sku` for all products missing a barcode
- Good bootstrapping tool to get existing inventory scan-ready without manual data entry

### 10. SKU vs Barcode are treated as distinct fields
- SKU is internal/required, barcode is optional/manufacturer-assigned
- The billing scanner lookup checks barcode first, then SKU — correct priority order

---

## MISSING — Things That Don't Exist Yet

### 1. No database index on `products.barcode`
- `smart_mart/models/product.py` defines indices for `sku`, `name`, `category`, `is_active`, `quantity` — but **no index on `barcode`**
- The `__table_args__` tuple shows: `db.Index("ix_product_sku", "sku")` exists but `ix_product_barcode` is absent
- For now this is hidden by the fact that lookup is done in JS (client-side), but if/when you add a server-side barcode API endpoint, table scans will be slow on large inventories
- Also `product_variants.barcode` has no index (only a UNIQUE constraint, which SQLite/Postgres creates an implicit index for — so variants are actually OK)

### 2. No server-side barcode lookup API endpoint
- There is no `/api/product/barcode/<code>` or similar route in `smart_mart/blueprints/api/routes.py`
- The existing `search_product_by_barcode()` in `operations_manager.py` (line 54) looks up a `ProductInventoryProfile.barcode` — **a different, separate barcode field** on an operations model, not `products.barcode` — and is not exposed as an HTTP endpoint at all
- All lookup is 100% client-side against the JS `PRODUCTS` array embedded on page load
- This means: if you have 2000+ products, they all get serialized into the HTML page on every billing screen load — no lazy fetch, no server-side lookup

### 3. No barcode-based search in the inventory list
- `inventory_manager.get_products(search=...)` searches by name, category, and exact SKU — **not by barcode**
  ```python
  stmt = stmt.where(or_(
      func.lower(Product.name).contains(term),
      func.lower(Product.category).contains(term),
      func.lower(Product.sku) == term,   # exact SKU only, no barcode
  ))
  ```
- Staff can't scan a product's barcode in the inventory search to find it

---

## NEEDS FIX — Things That Exist But Are Broken or Incomplete

### 1. Label printing uses SKU barcode value, not the `barcode` field
- `smart_mart/blueprints/inventory/routes.py` line ~1295:
  ```python
  barcode_b64 = _generate_barcode_b64(p.sku)   # ← uses SKU, not p.barcode
  ```
- The label template falls back correctly: `barcode: {{ (p.barcode or p.sku)|tojson }}`
- But the server-side PDF generation ignores `p.barcode` entirely and always uses the SKU value to generate the barcode image
- **Impact:** If a product has a real EAN-13 barcode (e.g. `8901234567890`) stored in `product.barcode`, the printed label shows a barcode for the SKU (`RICE-001`) instead — so scanning the label won't work with manufacturer barcodes

### 2. `search_product_by_barcode()` in operations_manager looks in the wrong table
- `smart_mart/services/operations_manager.py` line 54:
  ```python
  def search_product_by_barcode(barcode: str) -> Product | None:
      profile = db.session.execute(
          db.select(ProductInventoryProfile).where(
              ProductInventoryProfile.barcode == barcode.strip()
          )
      ).scalar_one_or_none()
  ```
- This searches `product_inventory_profiles.barcode`, not `products.barcode`
- `ProductInventoryProfile` is an operations/tracking model — its `barcode` field is populated separately and may not match what staff entered in the product form
- This function is not called from any route but if it were wired up, it would silently return `None` for most scans

### 3. Quick Product Picker search does include barcode — but only for in-stock items
- The picker's `renderGrid()` JS filters: `p.barcode && p.barcode.toLowerCase().includes(searchQuery)`
- This is correct, but only searches in-stock products (pre-filtered to `p.stock > 0`)
- If a product is out of stock it won't be found by barcode scan — the cashier gets a "not found" error even though the product exists

---

## RECOMMENDATION — Prioritized Fix List

### Priority 1 (blocking for real EAN/UPC barcodes): Fix label printing to use `product.barcode`
**File:** `smart_mart/blueprints/inventory/routes.py` ~line 1295
```python
# Change:
barcode_b64 = _generate_barcode_b64(p.sku)
# To:
barcode_b64 = _generate_barcode_b64(p.barcode or p.sku)
```
Without this, printed labels encode the SKU not the manufacturer barcode — scans fail.

### Priority 2 (quick win): Add barcode index to products table
**File:** `smart_mart/models/product.py` and `smart_mart/services/schema_migrations.py`
```python
# In Product.__table_args__:
db.Index("ix_product_barcode", "barcode"),
# In schema_migrations.py, add migration:
("2026_xx_xx_product_barcode_index",
 "Add index on products.barcode for fast barcode lookup",
 lambda conn: _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_product_barcode ON products(barcode)"),
),
```
Protects performance when inventory grows.

### Priority 3 (recommended): Add barcode to inventory search
**File:** `smart_mart/services/inventory_manager.py` line ~267
```python
stmt = stmt.where(or_(
    func.lower(Product.name).contains(term),
    func.lower(Product.category).contains(term),
    func.lower(Product.sku) == term,
    func.lower(Product.barcode) == term,   # ← add this
))
```
Lets staff scan a product to find it in inventory.

### Priority 4 (nice to have): Allow scanning out-of-stock products
Currently `handleBarcodeScan` only finds products in the JS `PRODUCTS` array, which only includes `quantity > 0` items. If the cashier scans a product that just ran out, they get "not found." Consider loading all active products (even zero-stock) into the array and surfacing a stock warning instead of a "not found" error.

### Priority 5 (for large catalogs): Add `/api/product/lookup?barcode=` endpoint
Once the catalog grows past ~500 products, embedding all of them in the HTML page on every billing screen load will become noticeably slow. A server-side barcode lookup endpoint lets you switch to on-demand lookup. Not urgent for small stores.

### Priority 6 (cleanup): Fix or remove `search_product_by_barcode` in operations_manager
Either wire it to `products.barcode` or delete it. It currently silently looks in the wrong table.

---

## Summary Table

| Area | Status |
|---|---|
| `products.barcode` DB field | ✅ Exists, migrated |
| `product_variants.barcode` DB field | ✅ Exists, unique |
| Barcode entry in product form | ✅ Working |
| Barcode entry in variant form | ✅ Working |
| Barcode in billing JS PRODUCTS array | ✅ Populated |
| Barcode input on POS screen | ✅ Exists |
| Scanner Enter-key handling | ✅ Implemented |
| Auto-timeout for scanners without Enter | ✅ Implemented |
| Lookup priority (barcode → SKU → name) | ✅ Correct |
| Qty increment on re-scan of same item | ✅ Works |
| Not-found error feedback | ✅ Red toast |
| F2 shortcut to focus scanner input | ✅ Works |
| Label printing barcode value | ❌ Uses SKU not `product.barcode` |
| DB index on `products.barcode` | ❌ Missing |
| Inventory list search by barcode | ❌ Not included |
| `search_product_by_barcode()` service | ❌ Wrong table |
| Server-side barcode lookup API | ❌ Not built (not yet needed) |
