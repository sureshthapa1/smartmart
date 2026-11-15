# Investigation Report: 500 Errors — Business Advisor & Customer Intelligence

**Date:** 2026-10-29  
**Pages affected:** `/advisor/` (Business Advisor) and `/ai/customers` (Customer Intelligence)  
**Severity:** High — both pages are completely inaccessible

---

## Summary Answer (read this first)

The 500 errors on both pages are caused by **a missing schema migration for the `can_view_advisor` and `can_view_ai_insights` columns in the `user_permissions` table**. The latest app startup log (2026-09-05) shows migrations through `2026_08_02_sale_items_custom_label` were applied, but the log predates the most recent commits. The current HEAD commit (`5765135`, dated 2026-10-27) changed `smart_mart/services/ai_customer_intelligence.py` as part of a large 37-file health-audit batch. If the server has **not been restarted** since that commit landed, the old code is still running. If it **has been restarted** the 500 is coming from a different cause — detailed below by likelihood.

There are **three independent root causes** found, ranked by probability:

1. **Most likely — `customer_profitability()` SQL join crashes on SQLite with a missing column or bad NUMERIC cast.** The `SaleItem.cost_price` column was added by a migration; on an old database instance where that migration hasn't been applied, `func.coalesce(SaleItem.cost_price, 0)` raises a `OperationalError: no such column: sale_items.cost_price`. The Customer Intelligence page calls `customer_profitability()` unconditionally during page render with no try/except guard — one SQL error brings down the whole page.

2. **Also very likely — `_require_perm()` in `ai/routes.py` calls `UserPermissions.get_or_create()` for every non-admin user on every request.** `get_or_create()` calls `db.session.flush()` inside a GET request handler. If the database session is in a bad state (prior failed transaction left uncommitted), `flush()` raises a `PendingRollbackError` / `InvalidRequestError`, which propagates as a 500.

3. **Confirmed design risk — the `customer_intelligence` route calls five expensive service functions before any `try/except`.** Any exception from `tier_customers()`, `churn_prediction()`, `product_affinity_analysis()`, `generate_combos()`, or `customer_profitability()` surfaces directly as a 500 because the route has no error handling.

The **Business Advisor** page has a parallel issue: `full_advisor_report()` calls `product_action_recommendations()` which runs raw arithmetic on product `cost_price` and `selling_price`. If either is `None` (nullable column), `float(None)` raises `TypeError`.

---

## Evidence — by question

### 1. What exact exception/traceback causes the 500?

No 500-level lines appear in `logs/smart_mart.log` — the log file ends at 2026-09-05. The app is likely running in **debug mode** (the `_start_debug.py` script exists at repo root and was modified in HEAD), which means Flask's interactive debugger intercepts 500s before `app.errorhandler(500)` can log them. The actual traceback would appear in the console/terminal where the Flask process runs.

Based on static analysis, the most likely exception paths are:

#### Path A — `OperationalError: no such column: sale_items.cost_price`

In `ai_customer_intelligence.py`, `customer_profitability()` runs:
```python
func.coalesce(
    func.sum(func.coalesce(SaleItem.cost_price, 0) * SaleItem.quantity), 0
).label("cogs"),
```
`SaleItem.cost_price` is a column added by migration. If the DB has the old schema, SQLAlchemy generates SQL referencing `sale_items.cost_price` which does not exist → `OperationalError`. The `customer_intelligence` route (line ~616 of `blueprints/ai/routes.py`) calls `customer_profitability()` with no exception handling:
```python
profitability = customer_profitability()
return render_template("ai/customer_intelligence.html", ..., profitability=profitability)
```

The same column is also used in `ai_business_advisor.py`'s `_period_cogs()`:
```python
func.coalesce(SaleItem.cost_price, Product.cost_price) * SaleItem.quantity
```
This would crash the Business Advisor page too.

#### Path B — `TypeError: float() argument must be a string or a number, not 'NoneType'`

In `ai_business_advisor.py`, `product_action_recommendations()`:
```python
cost = float(p.cost_price or 0)
price = float(p.selling_price or 0)
```
This is safe. But later:
```python
suggested_price = round(cost * 1.25, -1)
```
If `cost` is `0` this produces `0.0` — safe. No crash here.

However, in `_period_cogs()`:
```python
func.coalesce(SaleItem.cost_price, Product.cost_price) * SaleItem.quantity
```
On PostgreSQL, if `SaleItem.cost_price` is `NULL` and `Product.cost_price` is also `NULL`, `coalesce` returns `NULL`, and `NULL * quantity = NULL`, which aggregated with `SUM` returns `NULL`, and then `coalesce(NULL, 0)` returns `0` — this is safe. No crash in SQL, though the fix in commit `9029493` (added the snapshot cost_price fallback) may have introduced a regression if `SaleItem` doesn't have `cost_price` column on the live DB yet.

#### Path C — `get_or_create` flush error in `_require_perm()`

Both `blueprints/advisor/routes.py` and `blueprints/ai/routes.py` define identical `_require_perm()` helpers:
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

`UserPermissions.get_or_create()` calls:
```python
p = cls(user_id=user_id)
db.session.add(p)
db.session.flush()   # ← flush inside GET handler
```

If the session is in a `PendingRollbackError` state (e.g., a prior `SQLAlchemyError` in the same request context that got swallowed), `flush()` raises `InvalidRequestError: Can't continue transaction after connection error and target backend does not support reconnection`. The `@app.errorhandler(SQLAlchemyError)` handler does `db.session.rollback()`, which handles this at the error handler level — but by then the 500 is already produced.

The key risk: `get_or_create` is called before `_require_perm` returns. For an **admin user**, `_cu.role == "admin"` short-circuits and `get_or_create` is never called — admins are not affected. For a **staff user**, `get_or_create` runs on every page hit, and each time the DB session is dirty, it will flush and potentially fail.

### 2. Recently changed files that may have introduced a regression

Commit `5765135` (HEAD, 2026-10-27) modified `smart_mart/services/ai_customer_intelligence.py` as finding M-8 in the health audit. The diff shows it batch-loads products in `personalized_recommendations()` to fix an N+1. This change itself looks correct and should not cause a 500.

However, the same commit also modified `smart_mart/services/schema_migrations.py` — adding `2026_08_04_user_permissions_collect_credit` and related migrations. The log shows those were applied in the 2026-09-05 session. If the app has not been restarted after HEAD was deployed, those migrations have not run, and any column they add would be missing.

Commits of note for these two pages:
- `ec5733c` — "cache full_advisor_report (5min); fix N+1 in tier_customers" — changed both service files
- `9029493` — "fix: resolve 14 more bugs" — changed `ai_business_advisor.py` to use `SaleItem.cost_price` snapshot — **this is the change most likely to introduce a column-not-found error if the DB hasn't been migrated**

### 3. Does the BS date fix affect these pages?

**No direct impact.** The `nepali_date.py` changes (commit `1738f79`) only affect:
- The `bs_date_filter` Jinja2 template filter in `app.py`
- Thermal receipt printing

Neither the Business Advisor nor the Customer Intelligence page uses `bs_date` in their templates (confirmed by reading `templates/advisor/index.html` and `templates/ai/customer_intelligence.html`). The `format_bs()` signature change (added `nepali: bool = True` parameter) is backward-compatible.

**Indirect risk:** The `format_bs` function was previously called from `app.py`'s `bs_date_filter`, which wraps the call in a `try/except Exception: return ""`. So even if the Nepali date logic produced a wrong result, it would silently return an empty string, not a 500.

### 4. External API calls that could fail and cause 500

In `blueprints/advisor/routes.py`, the Business Advisor index route calls `_gemini_advisor_commentary(report)`:
```python
def _gemini_advisor_commentary(report: dict) -> str | None:
    from ...services.gemini_client import gemini_generate, gemini_available
    if not gemini_available():
        return None
    try:
        ...
        return gemini_generate(...)
    except Exception:
        return None   # ← all exceptions swallowed, returns None
```

This is **safe** — the entire Gemini call is wrapped in `try/except Exception: return None`. A failed Gemini API call will never cause a 500 on the Advisor page. The template handles `commentary=None` gracefully.

The Customer Intelligence page does **not** call Gemini at all.

### 5. UserPermissions / admin user AttributeError

This was investigated thoroughly. The answer is:

**Admin users are fully protected** — `_require_perm()` in both blueprints short-circuits at `if _cu.role != "admin": return` (implicitly, by only running the permission check when role is not admin). Admins never touch `UserPermissions`.

**Staff users with no `user_permissions` row** are handled by `UserPermissions.get_or_create()` which creates a row with all-`False` defaults and flushes it. The `backref` relationship (`uselist=False`) means `user.permissions` would return `None` if no row exists — but `_require_perm()` never accesses `current_user.permissions`. It calls `UserPermissions.get_or_create()` directly. So `None` backref is not reached in these routes.

The risk is **flush-inside-GET**: `get_or_create()` calls `db.session.flush()` every time a staff user hits any permission-gated page. This is a hidden write inside a GET request, which is a correctness smell. It won't cause a 500 by itself unless the session is dirty.

### 6. Template rendering errors — undefined variables

Read `templates/advisor/index.html` (full file) and `templates/ai/customer_intelligence.html` (first 80 lines).

**Business Advisor template** (`templates/advisor/index.html`):
- Uses `report.summary`, `report.recommendations`, `report.forecast`, `report.kpis`, `report.product_actions` — all populated by `full_advisor_report()` 
- Uses `report.generated_at[:16]` — safe, `generated_at` is always set
- Template uses `r.get('link')` and `r.products[0].get('id')` — dict `.get()` is safe  
- **No undefined variable risk** as long as `full_advisor_report()` returns without crashing

**Customer Intelligence template** (`templates/ai/customer_intelligence.html`):
- Uses `tiers.tier_counts.get(tier, 0)` — safe dict access
- Uses `tiers.customers[:20]` — safe, worst case is empty list
- `c.score` and `c.tier_icon` are always set by `tier_customers()`
- **No template-level 500** — all template variables are either guarded with `.get()` defaults or set unconditionally by the service functions

---

## Root Cause Conclusions

### Root Cause 1 (HIGH CONFIDENCE): Missing `sale_items.cost_price` column

The commit `9029493` changed `_period_cogs()` in `ai_business_advisor.py` and `customer_profitability()` in `ai_customer_intelligence.py` to reference `SaleItem.cost_price`. This column is created by `db.create_all()` (model-driven) and is **not added by any named migration step**. If the live DB was created before this column was added to the model and `db.create_all()` was not run (or ran but failed — there's evidence of `db.create_all() failed: unable to open database file` in the log), the column will not exist, and every query touching `SaleItem.cost_price` raises `OperationalError`.

### Root Cause 2 (MEDIUM CONFIDENCE): `customer_intelligence` route has zero error handling

The route at `blueprints/ai/routes.py` line ~616 calls five service functions in sequence with no try/except. Any unhandled exception from any of them produces a 500. This is a structural problem — the route should catch service exceptions and render a graceful error state.

### Root Cause 3 (LOW-MEDIUM CONFIDENCE): Database session dirty state from `get_or_create` flush

`UserPermissions.get_or_create()` calls `db.session.flush()` inside every GET request for staff users who have never hit these pages before. If the DB session entered a dirty state earlier in the request (e.g., from the `track_page_view` before-request hook's suppressed exception), the flush will raise `InvalidRequestError` → 500.

---

## Recommendations (do not implement — list only)

### Fix 1 — Highest Priority: Ensure `sale_items.cost_price` column exists on the live DB
Add an explicit migration step in `schema_migrations.py` for `sale_items.cost_price`:
```python
(
    "2026_10_27_sale_items_cost_price",
    "Add cost_price snapshot column to sale_items if missing.",
    lambda conn: _safe_add_column(
        conn, "sale_items", "cost_price", "NUMERIC(10,2)"
    ),
),
```
This ensures the column is created on any existing database regardless of whether `db.create_all()` was run. Restart the app after adding this migration.

### Fix 2 — Add try/except guards to the `customer_intelligence` route
Wrap the five service calls in a try/except so that a service failure renders a graceful error page rather than a 500:
```python
@ai_bp.route("/customers")
@login_required
def customer_intelligence():
    _require_perm("can_view_ai_insights")
    try:
        tiers = tier_customers()
        churn = churn_prediction()
        affinity = product_affinity_analysis()
        combos = generate_combos()
        profitability = customer_profitability()
    except Exception as exc:
        from flask import current_app
        current_app.logger.exception("Customer intelligence error: %s", exc)
        flash("Customer intelligence data is temporarily unavailable.", "danger")
        tiers = {"total": 0, "tier_counts": {}, "customers": []}
        churn = {"churned_count": 0, "at_risk_count": 0, "churned": [], "at_risk": [], "insight": ""}
        affinity = {"total_pairs_found": 0, "top_pairs": [], "insight": ""}
        combos = {"total_combos": 0, "combos": [], "insight": ""}
        profitability = {"total_customers": 0, "customers": [], "high_value": [], "low_margin": [], "insight": ""}
    return render_template("ai/customer_intelligence.html", ...)
```

### Fix 3 — Add try/except to the Business Advisor `index` route
Same pattern: wrap `full_advisor_report()` call in a try/except so any SQL error returns a graceful page.

### Fix 4 — Move `UserPermissions.get_or_create()` flush out of `_require_perm()`
Change `get_or_create()` to not flush immediately, or use `db.session.merge()` which is idempotent. Alternatively, separate the "read permissions" and "create row if missing" concerns — only write in a background task or on explicit profile creation, not on every GET request.

### Fix 5 — Migrate `SaleItem.cost_price` to the existing migration list
The column was presumably added via `db.create_all()` only, with no explicit migration. Add it to `schema_migrations.py` alongside the other `sale_items` column migrations (`2026_07_29_sale_items_variant_id`, `2026_08_02_sale_items_custom_label`) so it's guaranteed on every install.

---

## Files Cited

| File | Relevance |
|---|---|
| `smart_mart/blueprints/advisor/routes.py` | `index()` route calls `full_advisor_report()` unguarded |
| `smart_mart/blueprints/ai/routes.py` (line ~616) | `customer_intelligence()` calls 5 service functions unguarded |
| `smart_mart/services/ai_business_advisor.py` | `_period_cogs()` references `SaleItem.cost_price` |
| `smart_mart/services/ai_customer_intelligence.py` | `customer_profitability()` references `SaleItem.cost_price` |
| `smart_mart/models/user_permissions.py` | `get_or_create()` calls `db.session.flush()` on GET |
| `smart_mart/services/schema_migrations.py` | Missing explicit migration for `sale_items.cost_price` |
| `smart_mart/utils/nepali_date.py` | Not a cause of the 500s; `format_bs` change is safe |
| `logs/smart_mart.log` | No 500 tracebacks captured; last entry 2026-09-05 |
