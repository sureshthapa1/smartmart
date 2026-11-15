# SmartMart Fixes — Code Review

**Diff range:** `HEAD~5..HEAD` (single commit `cfe5032`)  
**Files changed:** `smart_mart/blueprints/inventory/routes.py` only (53 insertions, 29 deletions)  
**Coder-recorded verification:** `python -c 'from smart_mart.app import create_app; app = create_app(); print(OK)'` → OK

---

## Task 1 — Calibration once per server lifetime (GAPDETECT+CALIBRATE)

**Status: PASS**

`inventory_bp._label_calibrated = False` is set at module level (line ~19 of `routes.py`) with a clear comment explaining the intent. Inside `labels_print_direct`, the calibration block is guarded by `if not inventory_bp._label_calibrated:`. The block sends a `SIZE … GAP … GAPDETECT\r\nCALIBRATE\r\n` job to the printer, waits 3 seconds, then sets `inventory_bp._label_calibrated = True`.

Correctness notes confirmed:
- The flag lives on the Blueprint object, which persists for the lifetime of the Flask process. It resets on server restart as intended.
- The flag is set **after** the sleep, so a failed or interrupted calibration does not silently mark the printer as calibrated.
- Calibration fires before the item-printing loop, not inside it, so a multi-item job performs one calibration then prints all items in sequence.

No unintended behavior added.

---

## Task 2 — `PRINT N,1` → `PRINT N,0`

**Status: PASS**

The diff changes `lines.append(f'PRINT {copies},1')` to `lines.append(f'PRINT {copies},0')`.

A comment is present and accurate:
```python
# PRINT copies,0 — standard TSPL print command.
# The second argument is the "cut/tear" after-job feed count.
# 0 = no extra feed after the last label; gap alignment is
# handled by CALIBRATE (run once at server start), so the
# extra advance is not needed and wastes one sticker.
```

The change is documented, and the rationale is consistent with Task 1 (calibration handles gap alignment; the extra advance is redundant).

---

## Task 3 — `labels_pdf` default size 50×25; `labels.html` updated

**Status: PASS**

`routes.py` diff shows:
```diff
-    w_mm = float(data.get("w_mm", 75))
-    h_mm = float(data.get("h_mm", 50))
+    w_mm = float(data.get("w_mm", 50))
+    h_mm = float(data.get("h_mm", 25))
```

`labels.html` (unchanged in this diff — updated in a prior commit) confirms:
- CSS: `.lbl.s50x25 { width:50mm; height:25mm; padding:1mm 2mm; }` marked with `/* ★ Your sticker */`
- Size button: `<button class="sbtn on" data-s="s50x25" ...>★ 50×25mm (Your sticker)</button>` — `on` class marks it as the default active button
- JS: `let cur = 's50x25'; // default — matches actual physical sticker 50×25mm`
- JS SIZES map: `s50x25: { w:50, h:25, tip:'50 × 25 mm (Your sticker)' }`
- `DOMContentLoaded` calls `setSize('s50x25')` to initialize the preview

The PDF route's default now matches the HTML UI's default. Both sides are consistent at 50×25 mm.

---

## Task 4 — Advisor / Customer Intelligence pages no longer raise 500 errors

**Status: PASS (fix pre-exists before reviewed diff)**

The five root causes identified in `.agents/tasks/advisor-customer-intel-500.md` are addressed in commit `fd79d72` ("fix: dashain voucher off, BS date, staff perms, advisor 500s, purchases rollback"), which is outside the reviewed 5-commit window.

Evidence in current HEAD:

**`blueprints/advisor/routes.py`:** `index()` route wraps `full_advisor_report()` in `try/except Exception as exc`, logs the exception, flashes a user-visible message, and renders a safe empty `report` dict rather than propagating a 500.

**`blueprints/ai/routes.py`:** `customer_intelligence()` wraps all five service calls (`tier_customers`, `churn_prediction`, `product_affinity_analysis`, `generate_combos`, `customer_profitability`) in `try/except Exception as exc` with logging, a flash message, and safe empty fallback structures.

**`services/schema_migrations.py`:** Migration `2026_10_29_sale_items_cost_price` calls `_safe_add_column(conn, "sale_items", "cost_price", "NUMERIC(10,2)")` — this is an idempotent column-add that ensures the column exists on databases that pre-date the `2026_08_02` table-rebuild migration.

**`models/user_permissions.py`:** `get_or_create()` wraps `db.session.flush()` in `try/except Exception` with rollback and re-query — prevents `PendingRollbackError` from propagating as a 500 on staff user page hits.

All three root causes from the investigation (missing column, unguarded service calls, flush in GET) are fixed.

---

## Task 5 — Staff permission visibility works for users with granted permissions

**Status: PASS (fix pre-exists before reviewed diff)**

Also fixed in `fd79d72`. The `_require_perm()` helpers in both `blueprints/advisor/routes.py` and `blueprints/ai/routes.py` use:

```python
def _require_perm(perm: str):
    from flask import abort
    from flask_login import current_user as _cu
    if _cu.role != "admin":
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(_cu.id)
        if not getattr(p, perm, False):
            abort(403)
```

- Admin users short-circuit immediately — no DB access.
- Staff users with the permission granted (`can_view_advisor=True` or `can_view_ai_insights=True`) pass the `getattr` check and reach the route handler.
- Staff users without the permission get a 403, not a 500.
- `getattr(p, perm, False)` safely defaults to `False` if the column is absent (e.g., after a schema change that added a new permission column not yet flushed), preventing an `AttributeError`.

The `UserPermissions` model defines both `can_view_ai_insights` and `can_view_advisor` as `db.Column(db.Boolean, default=False)`, so granted permissions are readable immediately via `getattr`.

---

## Summary

All five tasks are satisfied. No blockers. No unintended behavior was introduced. The reviewed commit (`cfe5032`) touches only `routes.py` and addresses exactly tasks 1–3. Tasks 4–5 were fixed in the prior commit `fd79d72` and remain in effect at HEAD.
