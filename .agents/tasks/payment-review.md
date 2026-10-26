# eSewa default, split payment, and post-billing mode correction

Three commits (bff322e → e29f3a2) add eSewa as the default payment method on the billing screen, introduce a split Cash+eSewa payment mode, and allow correcting the payment mode after a sale is recorded. A handful of ancillary offer-UI improvements arrived in the same diff.

**Watch for:** (1) `update_payment_mode` allows switching *to* `credit` without setting `credit_due_date`, leaving a partial credit record. (2) The form action in `detail.html` is a hardcoded path (`/sales/{{ sale.id }}/payment-mode`) rather than `url_for`; this will silently break if the blueprint prefix changes or an application root is mounted. (3) The discount_note truncation guard fires at exactly the column limit (120 chars), but the error path re-renders `create.html` without the session cart state, so the user loses their cart and has to re-enter items — confirmed for split-note truncation and pre-existing for all other early-return paths.

**Verdict**: APPROVED

---

## High-level view

eSewa is now `checked` and Cash has `checked` removed — the diff confirms this is a clean swap with no other radio state changes. The `initPaymentPanels()` IIFE correctly hides the change calculator on load when eSewa is default, so the cash panel no longer flashes visible for a frame.

The split payment UI is gated client-side with `validateSplitAmounts()`, called on both input events and at form submit. The server records the breakdown in `discount_note` (e.g. `"Split: Cash NPR 250.00 + eSewa NPR 500.00"`) because no schema column exists for split amounts. The 120-char guard aligns exactly with the `String(120)` column definition — tight but correct.

`update_payment_mode` is `@login_required`, validates against an explicit allowlist, commits to both `payment_mode` and `payment_method`, and redirects back to the detail page. Non-admins are restricted to same-day / own sales, mirroring the void-sale permission pattern. Switching to `credit` updates the mode but leaves `credit_due_date` null and `credit_collected` defaulting to `False`, creating a partial credit record with no collection date.

The payment mode display in `detail.html` now covers all ten modes (cash, esewa, khalti, fonepay, qr, card, bank, credit, split, other) with distinct badge colours and emoji labels, including branded purple for Khalti and teal for split. The post-billing correction form carries a CSRF token via `{{ csrf_token() }}`.

No database migrations were made. `split`, `card`, and `other` were added to the `PAYMENT_METHODS` list in `sale.py`, which is used for display/reporting, not for database constraints.

---

<details>
<summary>Issues (4)</summary>

1. **Credit mode via update_payment_mode leaves credit_due_date null** — `update_payment_mode` allows switching to `credit` but never sets `credit_due_date`. Any code that reads `credit_due_date` on credit sales (collection reminders, overdue queries) will see null for sales corrected this way. Fix: either exclude `credit` from the `valid_modes` list in `update_payment_mode`, or add logic to set a default due date when the new mode is `credit`.

2. **Hardcoded URL in detail.html payment-mode form** — `action="/sales/{{ sale.id }}/payment-mode"` bypasses Flask's `url_for`. If the blueprint prefix changes or the app is mounted at a sub-path, this breaks silently. Fix: `action="{{ url_for('sales.update_payment_mode', sale_id=sale.id) }}"`.

3. **Cart loss on split-note truncation error** — When the 120-char guard fires, `render_template("sales/create.html", ...)` is called without the posted cart state. The user sees the billing form with an empty cart and must re-enter all items. Fix for the new path: redirect instead of re-render, consistent with POST-redirect-GET.

4. **Split amounts not validated server-side** — The server trusts the client-side sum check. A request bypassing the JS can submit split amounts that don't add up to the sale total, which gets committed silently. Low-risk for an internal POS but a future hardening target.

</details>

<details>
<summary>Details</summary>

### Credit mode via `update_payment_mode` — missing due date (confirmed)

`update_payment_mode` in `routes.py` (line 297) lists `"credit"` in `valid_modes` and will commit `payment_mode = "credit"` and `payment_method = "credit"` without touching `credit_due_date`. The `Sale` model has `credit_due_date = db.Column(db.Date, nullable=True)` and `credit_collected = db.Column(db.Boolean, nullable=False, default=False)`, so the row becomes a partial credit record: mode says credit, no due date, never collected. The original `create_sale` path has a full guard that defaults to 30 days from today; `update_payment_mode` has none. This won't raise a database error but will produce blank entries in any collection-reminder or overdue query that assumes `credit_due_date IS NOT NULL WHERE payment_mode='credit'`.

### Hardcoded route in detail.html (confirmed)

```html
<form method="POST" action="/sales/{{ sale.id }}/payment-mode">
```

`url_for('sales.update_payment_mode', sale_id=sale.id)` is the correct form. The hardcoded path works as long as `url_prefix="/sales"` stays unchanged and the app is mounted at root, which is currently true. The CSRF token is correctly present.

### Cart loss on truncation error (confirmed)

The early return for an overly long split note calls:

```python
return render_template(
    "sales/create.html",
    products=products,
    variants=variants,
    product_map=product_map,
    target_progress=target_progress,
)
```

`products` and `variants` are the product catalogue; the cart rows (items, quantities, prices) come from `request.form` and are not passed back. The user's cart is silently discarded. The pre-existing early-return paths (empty items, bad discount, no discount permission) have the same problem, but this PR adds a new instance triggered by a non-obvious limit (a long discount note combined with split payment).

### Split validation — server trusts client (confirmed)

`validateSplitAmounts()` uses `Math.round(...* 100) / 100` and tolerates a 0.01 NPR rounding gap. It fires on input events and at form submit. The server records the split breakdown in `discount_note` but does not re-validate the sum against the sale total. A user who bypasses the JS (curl, devtools) can submit mismatched amounts that get silently committed. Acceptable for an internal POS, but worth flagging as a future hardening target.

### Test coverage gap (confirmed)

No tests cover `update_payment_mode` — not the happy path, not the cashier ownership restriction, not the credit edge case. The split payment creation path (split note appended to `discount_note`) is also untested. Both are new routes/code paths with meaningful branching (role check, date check, mode allowlist, truncation guard).

</details>

---

<details>
<summary>File map</summary>

- `smart_mart/blueprints/sales/routes.py` — added split-note parsing in `create_sale`; added `update_payment_mode` route
- `smart_mart/models/sale.py` — added `split`, `card`, `other` to `PAYMENT_METHODS`
- `smart_mart/templates/sales/create.html` — eSewa default, split radio + panel, `validateSplitAmounts`, `initPaymentPanels`, per-offer remove buttons, `_syncAppliedOfferField`
- `smart_mart/templates/sales/detail.html` — full payment mode badge/label coverage, post-billing mode correction form with CSRF token

Full diff: `git diff 53ec7cc HEAD`

</details>
