# Implementation Plan — Payment Mode Improvements

## Summary of findings

| Item | Current state |
|---|---|
| Default payment radio | `cash` has `checked` (line 189 of create.html) |
| Change-due panel | Shown only when `isCash === true` (line 1980-1983 of create.html) |
| `changeDuePanel` initial state | `style="display:none;"` — hidden on load, shown after Cash radio event |
| Credit due date panel | shown when `isCredit` (same event listener, line 1968) |
| `PAYMENT_METHODS` in sale.py | 7 tuples: cash, fonepay, esewa, khalti, qr, bank, credit — no `split`, no `card`, no `other` |
| Payment badge in detail.html | invoice header covers cash/credit/qr/else; Sale Info panel covers cash/qr/card/credit/else |

---

## Item 1 — eSewa Default + Hide change panel for eSewa

### File: `smart_mart/templates/sales/create.html`

**Change A — Move `checked` from Cash to eSewa radio (line ~189 and ~193)**

Find the two radio inputs:
```html
<input class="form-check-input" type="radio" name="payment_mode" id="pm_cash" value="cash" checked>
...
<input class="form-check-input" type="radio" name="payment_mode" id="pm_esewa" value="esewa">
```

Change to:
```html
<input class="form-check-input" type="radio" name="payment_mode" id="pm_cash" value="cash">
...
<input class="form-check-input" type="radio" name="payment_mode" id="pm_esewa" value="esewa" checked>
```

**Change B — The `changeDuePanel` is already `display:none` on load, so it will naturally be hidden when eSewa is the initial selection. No change needed to the initial render.**

**Change C — The JS listener (line ~1980-1983) currently does:**
```js
const isCash = document.querySelector('input[name="payment_mode"]:checked')?.value === 'cash';
const changePanel = document.getElementById('changeDuePanel');
if (changePanel) changePanel.style.display = isCash ? '' : 'none';
```
This already hides the panel for eSewa — no change required to the `changeDuePanel` logic. The only risk was the initial state on page load: because `cash` was `checked` by default, the panel could briefly flash visible. After moving `checked` to eSewa it stays hidden on load. ✅

**No JS changes needed for Item 1 beyond moving `checked`.**

---

## Item 2 — Split Payment (Cash + eSewa)

### File: `smart_mart/models/sale.py`

Add `("split", "Split: Cash + eSewa")` to `PAYMENT_METHODS`. Also add `("card", "Card")` and `("other", "Other")` while here since they appear in the form but are missing from the list (needed for the detail page select in Item 3).

Current list ends at line 13. Replace the entire `PAYMENT_METHODS` list:

```python
PAYMENT_METHODS = [
    ("cash",   "Cash"),
    ("esewa",  "eSewa"),
    ("khalti", "Khalti"),
    ("fonepay","Fonepay"),
    ("qr",     "QR Code"),
    ("card",   "Card"),
    ("bank",   "Bank Transfer"),
    ("credit", "Credit / Udharo"),
    ("split",  "Split: Cash + eSewa"),
    ("other",  "Other"),
]
```

### File: `smart_mart/templates/sales/create.html`

**Change A — Add Split radio button** after the `pm_other` block (after line ~224):

```html
<div class="form-check form-check-inline">
  <input class="form-check-input" type="radio" name="payment_mode" id="pm_split" value="split">
  <label class="form-check-label" for="pm_split">💰 Split: Cash + eSewa</label>
</div>
```

**Change B — Add Split inputs panel** in the same customer card body, after the `#creditDueDateBox` block (around line ~230). Insert this new hidden div:

```html
<!-- Split Payment Inputs (shown when Split radio is selected) -->
<div id="splitPaymentBox" class="col-12" style="display:none;">
  <div class="border rounded-3 p-3" style="background:linear-gradient(135deg,#f0fdfa,#ccfbf1);border-color:#5eead4!important;">
    <div class="fw-semibold small mb-2">💰 Split Payment Breakdown</div>
    <div class="row g-2 align-items-end">
      <div class="col-md-4">
        <label class="form-label small">Cash Amount (NPR)</label>
        <input type="number" name="split_cash_amount" id="splitCashAmount"
               class="form-control form-control-sm" min="0" step="0.01" placeholder="0.00">
      </div>
      <div class="col-md-4">
        <label class="form-label small">eSewa Amount (NPR)</label>
        <input type="number" name="split_esewa_amount" id="splitEsewaAmount"
               class="form-control form-control-sm" min="0" step="0.01" placeholder="0.00">
      </div>
      <div class="col-md-4">
        <div id="splitValidationMsg" class="small fw-semibold mt-1"></div>
      </div>
    </div>
  </div>
</div>
```

**Change C — JS: toggle split panel in the existing payment_mode radio change listener**

The listener is at line ~1963. It currently handles:
1. Credit risk check
2. Credit due date box show/hide
3. Change-due panel show/hide (cash only)

Extend it to also handle Split and the split panel. Replace the existing listener body with:

```js
document.querySelectorAll('input[name="payment_mode"]').forEach(radio => {
  radio.addEventListener('change', () => {
    clearTimeout(riskCheckTimer);
    riskCheckTimer = setTimeout(checkCreditRisk, 300);

    const selected = document.querySelector('input[name="payment_mode"]:checked')?.value;

    // Show/hide credit due date field
    const isCredit = selected === 'credit';
    const dueDateBox = document.getElementById('creditDueDateBox');
    if (dueDateBox) {
      dueDateBox.style.display = isCredit ? '' : 'none';
      if (isCredit && !document.getElementById('creditDueDateInput').value) {
        const d = new Date(); d.setDate(d.getDate() + 30);
        document.getElementById('creditDueDateInput').value = d.toISOString().split('T')[0];
      }
    }

    // Show/hide change calculator (cash only)
    const isCash = selected === 'cash';
    const changePanel = document.getElementById('changeDuePanel');
    if (changePanel) changePanel.style.display = isCash ? '' : 'none';

    // Show/hide split payment panel
    const isSplit = selected === 'split';
    const splitBox = document.getElementById('splitPaymentBox');
    if (splitBox) splitBox.style.display = isSplit ? '' : 'none';
    if (!isSplit) {
      // Clear split inputs when navigating away
      const sc = document.getElementById('splitCashAmount');
      const se = document.getElementById('splitEsewaAmount');
      if (sc) sc.value = '';
      if (se) se.value = '';
      const msg = document.getElementById('splitValidationMsg');
      if (msg) msg.textContent = '';
    }
  });
});
```

**Change D — JS: live validation for split amounts**

Add a new block right after the listener above (still inside `<script>`):

```js
// ── Split payment live validation ─────────────────────────────────────
function validateSplitAmounts() {
  const splitBox = document.getElementById('splitPaymentBox');
  if (!splitBox || splitBox.style.display === 'none') return true; // not in split mode
  const cash  = parseFloat(document.getElementById('splitCashAmount')?.value)  || 0;
  const esewa = parseFloat(document.getElementById('splitEsewaAmount')?.value) || 0;
  const total = parseFloat(document.getElementById('grandTotal')?.textContent)  || 0;
  const msg   = document.getElementById('splitValidationMsg');
  const diff  = Math.abs(cash + esewa - total);
  if (diff > 0.01) {
    if (msg) {
      msg.textContent = `⚠️ Sum (NPR ${(cash + esewa).toFixed(2)}) ≠ Total (NPR ${total.toFixed(2)})`;
      msg.style.color = '#dc2626';
    }
    return false;
  } else {
    if (msg) {
      msg.textContent = '✓ Amounts match';
      msg.style.color = '#16a34a';
    }
    return true;
  }
}

document.getElementById('splitCashAmount')?.addEventListener('input', validateSplitAmounts);
document.getElementById('splitEsewaAmount')?.addEventListener('input', validateSplitAmounts);
```

**Change E — Block form submit if split is selected but amounts don't balance**

In the existing `saleForm` submit handler (starts with `document.getElementById('saleForm').addEventListener('submit', async function(e) {`), add this check **after** the `e.preventDefault()` line but **before** the stock-level check and AI validation, around the top of the handler:

```js
// Block submission if split mode amounts don't match total
const selectedMode = document.querySelector('input[name="payment_mode"]:checked')?.value;
if (selectedMode === 'split' && !validateSplitAmounts()) {
  const div = document.createElement('div');
  div.className = 'alert alert-danger ai-validation-alert';
  div.innerHTML = '<strong>❌ Split payment error:</strong> Cash + eSewa amounts must equal the grand total.';
  document.querySelectorAll('.ai-validation-alert').forEach(el => el.remove());
  document.getElementById('saleForm').prepend(div);
  window.scrollTo(0, 0);
  return;
}
```

### File: `smart_mart/blueprints/sales/routes.py`

**Change A — In `create_sale` POST handler, detect split and append to discount_note**

In the `try:` block of the POST handler, after the `_credit_due_date` block and before the `sale = sales_manager.create_sale(...)` call, add:

```python
# ── Split payment: build breakdown note ───────────────────────────────
_payment_mode = request.form.get("payment_mode") or request.form.get("payment_method", "cash")
_discount_note = request.form.get("discount_note", "").strip() or None

if _payment_mode == "split":
    try:
        _split_cash  = float(request.form.get("split_cash_amount",  0) or 0)
        _split_esewa = float(request.form.get("split_esewa_amount", 0) or 0)
        _split_note  = f"Split: Cash NPR {_split_cash:.2f} + eSewa NPR {_split_esewa:.2f}"
        _discount_note = (_discount_note + "; " + _split_note) if _discount_note else _split_note
    except (ValueError, TypeError):
        pass
```

Then change the `sales_manager.create_sale(...)` call to pass `_discount_note` instead of `request.form.get("discount_note", "").strip() or None`:

```python
sale = sales_manager.create_sale(
    items,
    user_id=current_user.id,
    customer_name=request.form.get("customer_name", "").strip() or None,
    customer_address=request.form.get("customer_address", "").strip() or None,
    customer_phone=request.form.get("customer_phone", "").strip() or None,
    payment_mode=_payment_mode,
    payment_method=request.form.get("payment_method") or _payment_mode,
    discount_amount=posted_discount,
    discount_note=_discount_note,
    wallet_redeem_points=int(request.form.get("wallet_redeem_points", 0) or 0),
    applied_customer_offer_ids=_parse_customer_offer_ids(request.form),
    credit_due_date=_credit_due_date,
)
```

Note: the existing code already uses `request.form.get("payment_mode") or request.form.get("payment_method", "cash")` for `payment_mode`. Replace it with `_payment_mode` variable introduced above. This is a refactor of the same two lines, not a logic change.

---

## Item 3 — Post-Billing Payment Mode Change

### File: `smart_mart/blueprints/sales/routes.py`

Add a new route at the end of the file, before the helper functions section (`# ---------------------------------------------------------------------------`):

```python
@sales_bp.route("/<int:sale_id>/payment-mode", methods=["POST"])
@login_required
def change_payment_mode(sale_id):
    """Allow any logged-in user (cashier or admin) to correct the payment mode after billing."""
    sale = sales_manager.get_sale(sale_id)
    if sale is None:
        flash("Sale not found.", "danger")
        return redirect(url_for("sales.list_sales"))

    new_mode = request.form.get("new_payment_mode", "").strip()
    valid_modes = [m[0] for m in Sale.PAYMENT_METHODS]
    if not new_mode or new_mode not in valid_modes:
        flash("Invalid payment mode selected.", "danger")
        return redirect(url_for("sales.sale_detail", sale_id=sale_id))

    sale.payment_mode   = new_mode
    sale.payment_method = new_mode
    db.session.commit()

    # Human-readable label for flash message
    label_map = dict(Sale.PAYMENT_METHODS)
    flash(f"Payment mode updated to {label_map.get(new_mode, new_mode)}.", "success")
    return redirect(url_for("sales.sale_detail", sale_id=sale_id))
```

Add the `Sale` import at the top of routes.py. Find the existing models imports section and add:
```python
from ...models.sale import Sale
```

### File: `smart_mart/templates/sales/detail.html`

**Change A — Add "Change Payment Mode" button and inline form in the Sale Info card**

Locate the Payment Mode `<dd>` block (roughly line 118-124):

```html
<div class="col-sm-4 mb-2">
  <dt>Payment Mode</dt>
  <dd>
    {% set pm = sale.payment_method or sale.payment_mode or 'cash' %}
    {% if pm == 'cash' %}💵 Cash
    {% elif pm == 'qr' %}📱 QR / Digital
    {% elif pm == 'card' %}💳 Card
    {% elif pm == 'credit' %}📋 Credit / Udharo
    {% else %}🔄 Other{% endif %}
  </dd>
</div>
```

Replace the entire `<div class="col-sm-4 mb-2">` block that wraps Payment Mode with:

```html
<div class="col-sm-4 mb-2">
  <dt>Payment Mode</dt>
  <dd>
    {% set pm = sale.payment_method or sale.payment_mode or 'cash' %}
    {% if pm == 'cash' %}💵 Cash
    {% elif pm == 'esewa' %}💚 eSewa
    {% elif pm == 'khalti' %}⚡ Khalti
    {% elif pm == 'fonepay' %}📲 Fonepay
    {% elif pm == 'qr' %}📱 QR / Digital
    {% elif pm == 'card' %}💳 Card
    {% elif pm == 'bank' %}🏦 Bank Transfer
    {% elif pm == 'credit' %}📋 Credit / Udharo
    {% elif pm == 'split' %}💰 Split: Cash + eSewa
    {% else %}🔄 Other{% endif %}
    <button type="button" class="btn btn-sm btn-outline-secondary ms-2 py-0"
            style="font-size:.72rem;"
            onclick="document.getElementById('changePaymentModeForm').style.display=document.getElementById('changePaymentModeForm').style.display==='none'?'':'none'">
      <i class="bi bi-pencil me-1"></i>Change
    </button>
    <!-- Inline payment mode correction form -->
    <div id="changePaymentModeForm" style="display:none;margin-top:.5rem;">
      <form method="post" action="{{ url_for('sales.change_payment_mode', sale_id=sale.id) }}"
            class="d-flex align-items-center gap-2 flex-wrap">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <select name="new_payment_mode" class="form-select form-select-sm" style="width:auto;">
          <option value="cash"   {% if pm == 'cash'    %}selected{% endif %}>💵 Cash</option>
          <option value="esewa"  {% if pm == 'esewa'   %}selected{% endif %}>💚 eSewa</option>
          <option value="khalti" {% if pm == 'khalti'  %}selected{% endif %}>⚡ Khalti</option>
          <option value="fonepay"{% if pm == 'fonepay' %}selected{% endif %}>📲 Fonepay</option>
          <option value="qr"     {% if pm == 'qr'      %}selected{% endif %}>📱 QR / Other</option>
          <option value="card"   {% if pm == 'card'    %}selected{% endif %}>💳 Card</option>
          <option value="bank"   {% if pm == 'bank'    %}selected{% endif %}>🏦 Bank Transfer</option>
          <option value="credit" {% if pm == 'credit'  %}selected{% endif %}>📋 Credit / Udharo</option>
          <option value="split"  {% if pm == 'split'   %}selected{% endif %}>💰 Split: Cash + eSewa</option>
          <option value="other"  {% if pm == 'other'   %}selected{% endif %}>🔄 Other</option>
        </select>
        <button type="submit" class="btn btn-sm btn-primary">Save</button>
        <button type="button" class="btn btn-sm btn-outline-secondary"
                onclick="document.getElementById('changePaymentModeForm').style.display='none'">Cancel</button>
      </form>
    </div>
  </dd>
</div>
```

---

## Item 4 — Payment Mode Badge/Label Display Fix

### File: `smart_mart/templates/sales/detail.html`

**Change A — Invoice header badge (lines ~95-101)**

Current:
```html
{% set pm = sale.payment_method or sale.payment_mode or 'cash' %}
<div class="mt-1">
  <span class="badge {% if pm == 'cash' %}bg-success{% elif pm == 'credit' %}bg-warning text-dark{% elif pm == 'qr' %}bg-info text-dark{% else %}bg-secondary{% endif %}">
    {% if pm == 'cash' %}💵{% elif pm == 'qr' %}📱{% elif pm == 'card' %}💳{% elif pm == 'credit' %}📋{% else %}🔄{% endif %}
    {{ pm | capitalize }}
  </span>
</div>
```

Replace with:
```html
{% set pm = sale.payment_method or sale.payment_mode or 'cash' %}
<div class="mt-1">
  {% if pm == 'cash' %}
    <span class="badge bg-success">💵 Cash</span>
  {% elif pm == 'esewa' %}
    <span class="badge bg-success">💚 eSewa</span>
  {% elif pm == 'khalti' %}
    <span class="badge" style="background:#5c2d91;color:#fff;">⚡ Khalti</span>
  {% elif pm == 'fonepay' %}
    <span class="badge bg-info text-dark">📲 Fonepay</span>
  {% elif pm == 'qr' %}
    <span class="badge bg-info text-dark">📱 QR / Digital</span>
  {% elif pm == 'card' %}
    <span class="badge bg-primary">💳 Card</span>
  {% elif pm == 'bank' %}
    <span class="badge bg-secondary">🏦 Bank Transfer</span>
  {% elif pm == 'credit' %}
    <span class="badge bg-warning text-dark">📋 Credit / Udharo</span>
  {% elif pm == 'split' %}
    <span class="badge" style="background:#0d9488;color:#fff;">💰 Split</span>
  {% else %}
    <span class="badge bg-secondary">🔄 Other</span>
  {% endif %}
</div>
```

**Change B — Sale Info "Payment Mode" dd text (already replaced in Item 3, Change A above — no separate change needed)**

The `<dd>` block for Payment Mode is fully replaced in Item 3 Change A with all correct labels. ✅

---

## Verification

After implementing all changes run:

```bash
# From the project root
python -m flask --app smart_mart db check    # confirms no migration needed (no schema change)
python -m flask --app smart_mart routes      # confirms /sales/<id>/payment-mode route is registered
```

Manual smoke test checklist:
1. Open `/sales/create` — eSewa radio must be pre-selected (not Cash).
2. Click "💰 Split: Cash + eSewa" — the Split panel must appear and the change-due panel must remain hidden.
3. Enter split amounts that don't sum to total — form must be blocked with the red error alert.
4. Enter correct split amounts — form submits, sale detail shows `discount_note` like "Split: Cash NPR X + eSewa NPR Y".
5. Open any sale detail — the "Change" button must appear next to Payment Mode.
6. Change to a different mode and Save — page reloads with a success flash and the updated badge.
7. Invoice header badge must show correct color for eSewa (green), Khalti (purple), Fonepay (info/teal), Split (teal), Bank (secondary).

---

## File change summary

| File | Changes |
|---|---|
| `smart_mart/models/sale.py` | Replace `PAYMENT_METHODS` list — add split, card, other |
| `smart_mart/templates/sales/create.html` | Move `checked` to eSewa; add Split radio; add `#splitPaymentBox`; extend radio listener; add `validateSplitAmounts()`; add submit-guard |
| `smart_mart/blueprints/sales/routes.py` | Import `Sale`; introduce `_payment_mode` / `_discount_note` vars in POST handler; add `change_payment_mode` route |
| `smart_mart/templates/sales/detail.html` | Replace Payment Mode `<dd>` with full label set + inline correction form; replace invoice header badge block with full color set |
