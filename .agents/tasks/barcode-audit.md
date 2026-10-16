# Barcode System Audit — Smart Mart

**Date:** Investigation complete  
**Scope:** Label printing, POS scanning, data model, search, autofill, migrations

---

## Summary

The barcode system is largely functional but has **three confirmed bugs** that directly explain the user's complaints (font overlapping, empty fields on 75×50mm labels) plus several issues that could cause silent scan failures at POS. There are no blocking database or migration failures, but the `ProductInventoryProfile.barcode` field is a dead-code orphan that can cause confusion.

---

## Findings by Area

---

### 1. Label Printing — `/labels/pdf` (PDF path)
**File:** `smart_mart/blueprints/inventory/routes.py` lines 1330–1493

#### BUG 1 — Font overlap on 75×50mm labels (HIGH — directly matches user complaint)

**Symptom:** User reports "fonts overlaying with each other" when printing on 75×50mm stickers.

**Root cause:** The PDF renderer draws lines from top downward using `y_cursor -= size_pt`, but the `extra` spacing calculation is capped at `1.5 * mm` regardless of label height. On a 75×50mm label there are 5–7 lines of text (shop name, product name, price, MRP, PKD, EXP, SKU) and the total line height can exceed the available text zone, driving `y_cursor` negative (below barcode) or causing lines to overlap.

```python
# routes.py ~line 1430 (approximate)
extra = max(0.0, (text_zone_h - total_line_h) / max(len(lines), 1))
extra = min(extra, 1.5 * mm)  # cap so it doesn't over-spread
```

The cap at `1.5 * mm` is fine when there is space, but when `total_line_h > text_zone_h` then `extra` becomes 0 yet `y_cursor` is still decremented by the full `size_pt` per line. There is no overflow guard — lines march into negative `y_cursor` territory or into the barcode zone.

**Fix:** Add a minimum-font-size fallback and an overflow clip. If `total_line_h > text_zone_h`, scale all font sizes proportionally (or drop lower-priority lines like MRP, SKU) before drawing.

---

#### BUG 2 — PKD / EXP fields display blank on 75×50mm even when dates exist (HIGH — matches user complaint)

**Symptom:** User reports "PKD, EXP and other spaces still empty."

**Root cause in `labels.html` JS template:** The `PRODS` dict is populated server-side in the Jinja template:

```html
<!-- labels.html ~line 172 -->
{{ p.id }}: { ..., pkg:{{ (p.pkg_date.strftime('%Y-%m-%d') if p.pkg_date else '')|tojson }}, 
              exp:{{ (p.expiry_date.strftime('%Y-%m-%d') if p.expiry_date else '')|tojson }} }
```

This is correct. **However**, the `buildPayload()` function constructs the JSON sent to `/labels/pdf`:

```javascript
// labels.html ~line 270
items.push({ pid, name:p.name, sku:p.sku, barcode:p.barcode||p.sku, price:p.price, 
             copies:n, pkg:p.pkg||'', exp:p.exp||'' });
```

The server reads these as `pkg_date = str(item.get("pkg", ""))` and `exp_date = str(item.get("exp", ""))` — **this matches correctly**.

**The actual cause of blank fields is the `DOMContentLoaded` size override.** The JS variable `cur` is initialized to `'s50x25'` but the `DOMContentLoaded` handler immediately calls `setSize('s75x50')`, which triggers `rebuild()`. When the page first loads no products are checked yet, so `rebuild()` produces nothing. But the key problem is:

```javascript
// labels.html line 324
document.addEventListener('DOMContentLoaded', ()=>{ setSize('s75x50'); });
```

The JS variable `cur` defaults to `'s50x25'` (line 186), but the UI button for 50×25mm is shown `.on` in the HTML (`data-s="s50x25"` with `class="sbtn on"`). The `DOMContentLoaded` handler switches to 75×50mm **without the user clicking it**, making the UI show 50×25 as "active" while `cur` is actually `s75x50`. This mismatch means the `@page` CSS injected for print is `75x50mm` but the button highlight still shows 50×25mm, confusing the user about which size will actually print.

**More critically:** the PDF payload's `w_mm`/`h_mm` sent to the server is derived from `SIZES[cur]` at the moment of click — which correctly uses 75×50 after `DOMContentLoaded`. So the PDF page size is correct. **The overlap problem is purely the server-side layout bug (BUG 1)** when fitting 5+ text lines into the available zone.

**Fix:** Align the JS default (`cur = 's75x50'`) to match `DOMContentLoaded`, or vice-versa.

---

#### BUG 3 — `show_dates` flag ignored when `pkg` / `exp` are empty strings (MEDIUM)

In the PDF renderer:
```python
if show_dates and exp_date:
    lines.append((f"EXP: {exp_date}", "Helvetica", 6.5))
if show_dates and pkg_date:
    lines.append((f"PKD: {pkg_date}", "Helvetica", 6.5))
```

This is correct — empty strings evaluate falsy. No bug here. But the order is **reversed** compared to the on-screen preview:

- **PDF prints:** EXP first, then PKD (bottom of text block, drawn upward)
- **HTML preview shows:** PKD first, then EXP

The `lines` list is built bottom-to-top (reversed at end), and EXP is appended before PKD. After the `lines.reverse()`, PKD ends up printed above EXP in the PDF. The HTML preview has `PKD` above `EXP` too (using `.lsku` divs top-down). These actually **match** — no visual inconsistency here. Low priority.

---

#### Code Quality — `draw_line` helper defined but unused (LOW)

```python
# routes.py ~line 1415
def draw_line(text, font, size_pt, y_bottom, truncate=True):
    ...
```

This inner function is defined but the actual drawing loop uses inline `c.setFont / c.drawString` calls instead. Dead code — can be removed.

---

### 2. Labels Template — `labels.html`

#### BUG 4 — Default size mismatch: JS `cur` vs HTML button state vs `DOMContentLoaded` (MEDIUM)

Three inconsistent declarations:
1. HTML button: `data-s="s50x25"` has class `sbtn on` — visually shows 50×25mm as default
2. JS variable: `let cur = 's50x25';` — logic default matches HTML
3. `DOMContentLoaded`: `setSize('s75x50')` — immediately overrides to 75×50mm

**Net effect:** Page renders with 50×25mm button highlighted, but internally `cur = 's75x50'` after DOM load. If a user immediately clicks "Download PDF" without touching size buttons, the PDF is 75×50mm but they thought they were printing 50×25mm. This is a UX bug that would cause wrong-size prints without any error.

**Fix:** Either change the HTML button default to `s75x50` (remove `on` from s50x25, add it to s75x50) OR change the `DOMContentLoaded` to call `setSize('s50x25')` if 50×25 is the user's actual sticker.

---

#### Improvement — No barcode pre-load on page load (MEDIUM)

Products pre-selected from a POST request have `selected_ids` populated by the server, but barcode images in `BC` are only loaded when a checkbox is clicked (`onChk` → `loadBc`). Pre-selected products (from POST form) need a separate initial load loop; currently they show no barcode in the preview until the checkbox is toggled.

```javascript
// labels.html: PRE_BC is populated for label_data items from POST, 
// but this only happens when the form is submitted first (GET render has label_data = [])
const PRE_BC = {
    {% for item in label_data %}{{ item.id }}: {{ item.barcode_b64 | tojson }}{% if not loop.last %},{% endif %}{% endfor %}
};
```

On a GET request, `label_data = []`, so `PRE_BC` is always empty. The barcode images are only lazily fetched per-checkbox-click. This means the preview always shows "no barcode" until the user toggles a checkbox — low impact since toggle is required anyway.

---

### 3. Labels Template — `print-direct` path

**File:** `smart_mart/blueprints/inventory/routes.py` lines 1495–1636

The direct-print path (Windows GDI + PIL) reads `pkg`/`exp` from the request JSON correctly:
```python
pkg = str(item.get("pkg", ""))
exp = str(item.get("exp", ""))
if pkg: put(f"PKD: {pkg}", f_small)
if exp: put(f"EXP: {exp}", f_small)
```

This mirrors what `buildPayload()` sends. **No bug here** — dates will appear if the product has them.

The direct-print path uses absolute pixel coordinates on a 240×160px landscape canvas. Font sizes (16, 22, 32, 13px) on a 240px-wide canvas are proportional and should not overlap. The Pillow `put()` helper truncates text to fit width. **No overlap bug exists in the direct-print path** — this confirms the overlap is PDF-only (BUG 1).

---

### 4. Billing/POS Scan Flow — `create.html`

**File:** `smart_mart/templates/sales/create.html`

#### `findProductBySku` — lookup logic

```javascript
function findProductBySku(query) {
    const q = query.trim();
    if (!q) return null;
    // 1. Exact barcode match (manufacturer EAN/UPC)
    let found = PRODUCTS.find(p => p.barcode && p.barcode === q);
    // 2. Exact SKU match (case-insensitive)
    if (!found) found = PRODUCTS.find(p => p.sku && p.sku.toLowerCase() === q.toLowerCase());
    // 3. Exact name match
    if (!found) found = PRODUCTS.find(p => p.name.toLowerCase() === q.toLowerCase());
    // 4. Partial name match
    if (!found) found = PRODUCTS.find(p => p.name.toLowerCase().includes(q.toLowerCase()));
    return found;
}
```

Step 1 uses **case-sensitive** barcode match (`p.barcode === q`). If a barcode was stored in uppercase (e.g. `RICE-001`) but the scanner outputs lowercase (unlikely for EAN/UPC but possible for SKU-as-barcode), it would silently fall through to step 2.

**More importantly:** The `PRODUCTS` array is populated at page load from the server. Products added to inventory **after** the POS page was opened will not appear. This is expected JS behavior but can confuse cashiers ("I just added this product, why won't it scan?").

#### Timer race condition (MEDIUM)

```javascript
barcodeInput.addEventListener('input', function() {
    clearTimeout(barcodeTimer);
    barcodeTimer = setTimeout(() => {
        const val = this.value.trim();
        if (val.length >= 3) handleBarcodeScan(val);
    }, 300);
});
```

The `input` event fires a 300ms timer. The `keydown` Enter handler fires immediately. If a scanner sends characters + Enter very quickly (< 300ms total), both fire: `keydown` processes the complete barcode immediately and clears `barcodeInput.value = ''`. Then 300ms later the timer fires with `val = ''` which is `< 3` chars so no harm. **No bug here — the race is benign.**

However, if the user manually types (not scanning), the 300ms timer fires after they stop typing, potentially triggering a scan mid-typing if they pause for 300ms. This causes false "not found" errors that confuse cashiers. **Recommendation:** Only auto-fire the timer if the input appears to be from a scanner (rapid input, no keyboard repeat events), or raise the threshold to 500ms and require length ≥ 6.

#### Out-of-stock handling (MEDIUM)

`findProductBySku` has no stock check. A product with `stock: 0` can be scanned and added to the cart. The client-side `checkStockLevels()` runs on submit and blocks zero-stock submissions, but the UX is poor: the item appears added successfully then fails on submit. **Recommendation:** Add a stock check inside `handleBarcodeScan` before calling `addRow`.

```javascript
if (product.stock <= 0) {
    // show "OUT OF STOCK: {product.name}" in the error toast instead of silently adding
}
```

#### Screen lock during scanning (matches user complaint #2, now resolved)

The user reported "lock screen pops up during scanning." This is a Windows/hardware issue, not a code bug — the scanner sends keyboard input which Windows interprets as activity for its idle-lock timer, but on some configurations the scan keystrokes reset the inactivity counter mid-session. This is resolved by adjusting Windows screen lock timeout or using the scanner's "sleep mode off" setting. **No code fix required.**

---

### 5. Data Model — `product.py` / `product_variant.py`

**File:** `smart_mart/models/product.py`

```python
barcode = db.Column(db.String(80), nullable=True)  # EAN-13/UPC manufacturer barcode
```

**No `unique=True` constraint on `Product.barcode`.** The model defines `ix_product_barcode` as a plain index (not unique). This means multiple products can share the same barcode value — a scan would return whichever `Product` SQLAlchemy finds first, which may not be deterministic.

**Contrast with `ProductVariant`:**
```python
barcode = db.Column(db.String(80), nullable=True, unique=True)
```

Variants enforce uniqueness; products do not. This is an inconsistency.

**Impact:** If two products accidentally get the same barcode (e.g. both use their SKU as barcode via autofill and two products have the same SKU pattern), a scan could pull the wrong product. The `autofill-barcodes` endpoint sets `barcode = sku`, and SKUs are not unique in the model (`sku` has an index but no `unique=True` on `Product` either — `db.Index("ix_product_sku", "sku")`). 

**Recommendation:** Add `unique=True` to `Product.barcode` (allowing NULL) and add a migration step.

---

### 6. Inventory Manager Search — `inventory_manager.py`

**File:** `smart_mart/services/inventory_manager.py`

```python
def get_products(search: str | None = None, ...) -> list[Product]:
    if search:
        term = search.strip().lower()
        _conditions = [
            func.lower(Product.name).contains(term),
            func.lower(Product.category).contains(term),
            func.lower(Product.sku) == term,
            func.lower(Product.barcode) == term,   # ← exact match
        ]
        if len(term) >= 4:
            _conditions.append(Product.barcode.ilike(f'%{term}%'))  # ← partial match for 4+ chars
```

Barcode is included in search — exact match for any length, partial match for 4+ chars. **No bug.**

However, SKU uses `==` (exact) while name/category use `.contains()` (partial). A search for `RICE` will match products named "Basmati Rice" but not a product with SKU "BASMATI-RICE-001". This is intentional for SKU (you must type the exact SKU) but may surprise users who expect SKU partial match. Low priority.

---

### 7. Auto-fill Barcodes — `POST /inventory/autofill-barcodes`

**File:** `smart_mart/blueprints/inventory/routes.py`

```python
@inventory_bp.route("/autofill-barcodes", methods=["POST"])
@admin_required
def autofill_barcodes():
    products_without_barcode = db.session.execute(
        db.select(Product)
        .where(Product.is_active == True, Product.barcode == None)
    ).scalars().all()
    updated = 0
    for p in products_without_barcode:
        if p.sku:
            p.barcode = p.sku   # use SKU as the barcode value
            updated += 1
    if updated:
        db.session.commit()
```

#### BUG 5 — No duplicate barcode guard (MEDIUM)

If two products have the same SKU (which the model allows — no unique constraint on `Product.sku`), this loop sets both their `barcode` fields to the same SKU value. Since `Product.barcode` also has no unique constraint, no IntegrityError is raised and both records get the same barcode. Scanning will then return whichever SQLAlchemy returns first.

**Fix:** Before setting `p.barcode = p.sku`, check that no other product already has that barcode value.

#### Edge case — SKUs that are not valid Code128 strings (LOW)

Code128 can encode any ASCII 0x20–0x7F. Most SKUs are safe, but SKUs with characters like `\t`, `\n`, or non-ASCII (Devanagari names) would cause the barcode library to raise. `_generate_barcode_b64` catches exceptions and returns `""` (or falls back to QR), so label generation degrades gracefully. The autofill still sets the barcode value even if the value won't render as a scannable barcode. **No crash, but silent quality degradation.**

---

### 8. `search_product_by_barcode` in `operations_manager.py`

**File:** `smart_mart/services/operations_manager.py`

```python
def search_product_by_barcode(barcode: str) -> Product | None:
    """Look up a product by its barcode field (EAN-13/UPC).
    Queries products.barcode directly — NOT product_inventory_profiles.barcode
    which is a separate field and may not match what staff entered in the product form.
    """
    return db.session.execute(
        db.select(Product).where(Product.barcode == barcode.strip())
    ).scalar_one_or_none()
```

This queries the correct table (`products.barcode`). The docstring explicitly calls out the pitfall of the other barcode field in `ProductInventoryProfile`.

**However: this function is not called anywhere in the codebase for POS scanning.** POS scanning is entirely client-side (JS `findProductBySku` in `create.html` searching the pre-loaded `PRODUCTS` array). `search_product_by_barcode` is unused dead code, potentially left over from a server-side scan API approach that was replaced by client-side matching.

```bash
# Confirmed: no call site found for search_product_by_barcode
grep -r "search_product_by_barcode" smart_mart/  # → only the definition in operations_manager.py
```

**Recommendation:** Either wire this into an API endpoint (`GET /api/scan-barcode?code=xxx`) for server-side fallback lookups, or remove it. As dead code it is misleading.

---

### 9. `ProductInventoryProfile.barcode` — Dead-Code Orphan Field

**File:** `smart_mart/services/operations_manager.py`

```python
def update_inventory_profile(product_id: int, barcode: str | None, ...):
    profile.barcode = (barcode or "").strip() or None
```

There exists a separate `barcode` field on `product_inventory_profiles` table (via `ProductInventoryProfile` model / `operations_manager.update_inventory_profile`). This field is **separate** from `Product.barcode` and is never consulted during label generation or POS scanning. The `search_product_by_barcode` docstring explicitly warns about this.

**Risk:** If a staff member uses the Operations page to update the inventory profile barcode thinking it will affect scanning, it will have zero effect. This is a data integrity / UX trap.

**Recommendation:** Either remove `ProductInventoryProfile.barcode` and deprecate `update_inventory_profile`'s barcode parameter, or add a migration to sync `product_inventory_profiles.barcode` → `products.barcode`.

---

### 10. Schema Migrations — `schema_migrations.py`

**File:** `smart_mart/services/schema_migrations.py`

Relevant barcode migration:
```python
(
    "2026_06_01_product_barcode",
    "Add barcode column to products for EAN-13/UPC scanner support.",
    lambda conn: _safe_add_column(conn, "products", "barcode", "VARCHAR(80)"),
),
```

And the index:
```python
# (inside 2026_08_xx performance migrations)
"CREATE INDEX IF NOT EXISTS ix_product_barcode ON products(barcode)"
```

**No uniqueness constraint is migrated.** The index is a plain `INDEX`, not `UNIQUE INDEX`. This is consistent with the model (no `unique=True` on `Product.barcode`) but means the DB does not enforce uniqueness.

**Findings:**
- `products.barcode` — column exists, plain index, no unique constraint ✓ (by design, but risky)
- `product_variants.barcode` — defined in model as `unique=True`; the migration file has no explicit migration step for this column, which means it was created by `db.create_all()` when the table was first created. The unique constraint should exist if the table was created after the `unique=True` was added to the model.
- No migration adds `products.barcode` as `UNIQUE` — consistent with model, but leaves data-quality gap.

---

## Prioritized Recommendations

### Blocking / High Impact

| # | Finding | File | Fix |
|---|---------|------|-----|
| 1 | **BUG 1: Font overlap on PDF labels** | `routes.py` `labels_pdf()` | Add text-block overflow guard: if `total_line_h > text_zone_h`, scale fonts or drop lowest-priority lines (SKU, MRP) before drawing |
| 2 | **BUG 4: Default size mismatch (50×25 shown, 75×50 active)** | `labels.html` | Change `DOMContentLoaded` to `setSize('s50x25')` to match the highlighted button and user's sticker |

### Medium Impact

| # | Finding | File | Fix |
|---|---------|------|-----|
| 3 | **BUG 5: No duplicate barcode guard in autofill** | `routes.py` `autofill_barcodes()` | Before `p.barcode = p.sku`, skip if another product already has that barcode |
| 4 | **`Product.barcode` has no uniqueness constraint** | `product.py`, `schema_migrations.py` | Add `UNIQUE INDEX IF NOT EXISTS ix_product_barcode_unique ON products(barcode) WHERE barcode IS NOT NULL` |
| 5 | **Out-of-stock product scan adds item silently** | `create.html` `handleBarcodeScan` | Check `product.stock <= 0` and show error toast before calling `addRow` |
| 6 | **Barcode case-sensitivity in POS scan** | `create.html` `findProductBySku` | Change step 1 to `p.barcode.toLowerCase() === q.toLowerCase()` |

### Low Impact / Code Quality

| # | Finding | File | Action |
|---|---------|------|--------|
| 7 | **`search_product_by_barcode` is dead code** | `operations_manager.py` | Wire into a `GET /api/scan` endpoint or remove |
| 8 | **`ProductInventoryProfile.barcode` is a confusing orphan** | `operations_manager.py` | Remove or document clearly as non-scanning field |
| 9 | **`draw_line` helper defined but unused in PDF renderer** | `routes.py` `labels_pdf()` | Remove dead function |
| 10 | **300ms auto-fire timer fires mid-manual-typing** | `create.html` | Raise threshold to 500ms, require length ≥ 6 for auto-fire |

---

## Direct Answers to User Complaints

| User complaint | Root cause | Code location |
|---------------|-----------|--------------|
| "Barcode not scanning during billing" | Resolved — likely product had no barcode value (NULL) before autofill was run; after autofill sets `barcode = sku`, scan matches via `findProductBySku` step 2 (SKU match) | `create.html` `findProductBySku` |
| "Lock screen pops up during scanning" | Windows idle-lock timer, not a code issue | OS-level setting |
| "Fonts overlaying with each other" | PDF text-block layout overflow when 5+ lines don't fit in the text zone above the barcode | `routes.py` `labels_pdf()` ~line 1430 |
| "PKD, EXP and other space still empty" | Caused by size mismatch: page loads as 75×50mm but if user never checked dates, `p.pkg` / `p.exp` are empty strings for products that have no dates set. If dates ARE set and still blank, confirm with BUG 1 — overflow pushes those small lines off the printable area | `labels.html` DOMContentLoaded + `routes.py` overflow bug |
