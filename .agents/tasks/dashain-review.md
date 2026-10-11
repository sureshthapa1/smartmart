# Dashain Lucky Draw Voucher and Offer Report

This change adds two user-facing features: a tear-off lucky draw voucher printed below every eligible thermal receipt, and a new `/reports/dashain-offer` page that splits all sales into above/below NPR 2000 segments with CSV export for each. The approach is additive — three new routes appended to `routes.py`, a new template, and a conditional block appended to the existing thermal receipt template. No existing routes or templates were modified.

Watch for: (1) the export URLs in `dashain_offer.html` build query strings by string-concatenating `url_for(…)` with raw template variables — this works, but is non-idiomatic and would break silently if `start_date` or `end_date` contain characters that need URL-encoding (unlikely for ISO dates but worth noting); (2) the main report route loads **all** sales for the date range into Python memory before splitting — no SQL-level `LIMIT` or pagination guard; (3) the voucher threshold (500) diverges from the report threshold (2000) — may be intentional, but the user's original request only mentions 2000 as the lucky draw boundary.

**Verdict**: NEEDS_CHANGES

---

## High-level view

The three new routes follow the established pattern exactly: `_require_perm("can_view_reports")`, `_get_date_range()`, model query, `render_template` or `Response(text/csv)`. The Sale model fields referenced — `invoice_number`, `customer_name`, `customer_phone`, `sale_date`, `total_amount`, `payment_mode`, `id` — are all confirmed present in the model. No existing routes were touched.

The export routes push the `total_amount >= 2000` / `< 2000` threshold down to SQL, which is correct. The main report route fetches everything and filters in Python; this is consistent with how several other report routes in the file work, but unlike those routes there is no `LIMIT` clause, so a large date range (e.g., "all time") on a busy store will pull every sale row into memory.

The export URLs in the template are constructed with `url_for(…) + "?start_date=…&end_date=…"` string concatenation. The idiomatic Flask/Jinja2 form passes the params directly to `url_for`, avoiding any encoding issue. The current approach is functionally safe for well-formed ISO date strings, but it is the only place in the codebase that does this — every other export link in the reports templates uses `url_for(…, start_date=…, end_date=…)`.

The voucher threshold in the receipt template (`>= 500`) and the report segmentation threshold (`2000`) are different numbers. The user's request describes above/below 2000 as the lucky draw boundary. A voucher printing at 500 means receipts for transactions between 500 and 1999 will carry a "Lucky Draw Voucher" even though those customers appear in the "Below 2000" segment of the report and would not be eligible for the lucky draw. This inconsistency will confuse customers.

The navigation link (`🪔 Dashain Offer`) was added to `base.html` inside the existing Reports submenu, guarded by `can_view_reports`, with active-state highlighting. Route name matches.

---

<details>
<summary>Issues (3)</summary>

1. **Voucher threshold vs. report threshold mismatch** — The voucher prints for `total_amount >= 500` but the lucky draw eligibility boundary in the report is 2000. Customers who spend 500–1999 receive a voucher labelled "Lucky Draw Voucher" but appear in the ineligible "Below 2000" segment. Change the voucher threshold to `>= 2000` (or whatever the confirmed eligibility floor is) to match the report boundary.

2. **Export URL string concatenation** — `{{url_for('reports.dashain_offer_export_above')}}?start_date={{start_date}}&end_date={{end_date}}` is non-idiomatic and inconsistent with every other export link in the reports section. Use `url_for('reports.dashain_offer_export_above', start_date=start_date, end_date=end_date)` to let Flask handle encoding and keep the pattern consistent.

3. **Unbounded memory load in main report route** — `dashain_offer_report` fetches all sales in the date range with no `LIMIT` and splits in Python. On a store with many transactions, a wide date range will load a large result set into memory. Add either a row cap (`.limit(2000)`) with a UI note, or add server-side pagination, consistent with the `credit_udharo` route which uses `.limit(500)`.

</details>

<details>
<summary>Details</summary>

### Voucher threshold vs. report threshold mismatch

The thermal receipt template gates the voucher block on `sale.total_amount|float >= 500`. The Dashain offer report segments customers at NPR 2000 — above is "Lucky Draw Eligible", below is "All Customers". A customer spending NPR 800 walks away with a physical voucher reading "Dashain Lucky Draw Voucher" but is recorded in the below-2000 (ineligible) list in the report. When lucky draw time arrives, that customer will present a voucher that the report says shouldn't win. The threshold comment in the receipt (`Change the threshold below (500) as needed`) acknowledges this is adjustable, making the fix straightforward — but as shipped it is a logical inconsistency in the feature.

### Export URL string concatenation

The two export buttons in `dashain_offer.html` are:

```html
<a href="{{url_for('reports.dashain_offer_export_above')}}?start_date={{start_date}}&end_date={{end_date}}"
```

Every other export link in the reports templates (e.g., `payment_breakdown.html`, `top_products.html`) passes date params as `url_for` arguments:

```html
href="{{ url_for('reports.export_sales_csv', start_date=start_date, end_date=end_date) }}"
```

The string-concatenation form works for ISO dates but bypasses Flask's URL encoding, breaks the codebase's own convention, and would silently produce malformed URLs if `start_date` or `end_date` were ever empty strings (the `?` would still appear, yielding `?start_date=&end_date=`).

### Unbounded memory load in main report route

```python
all_sales = db.session.execute(
    db.select(Sale)
    .where(and_(Sale.sale_date >= start, Sale.sale_date <= end))
    .order_by(Sale.sale_date.desc())
).scalars().all()
```

No row limit. The default date range is 30 days (`_get_date_range` fallback), but users can pass arbitrary `start_date`/`end_date`. The `credit_udharo` route, written by the same codebase, adds `.limit(500)` with a comment noting "prevents memory spike on large shops". The same guard belongs here.

</details>

---

## File map

<details>
<summary>Files changed</summary>

- `smart_mart/templates/sales/thermal_receipt.html` — Dashain tear-off voucher block added after the footer, guarded by `total_amount >= 500`
- `smart_mart/blueprints/reports/routes.py` — Three new routes appended: `dashain_offer_report` (HTML), `dashain_offer_export_above` (CSV), `dashain_offer_export_below` (CSV)
- `smart_mart/templates/reports/dashain_offer.html` — New report template with summary cards, two segmented tables, print support, and date-range filter
- `smart_mart/templates/base.html` — Navigation link added to Reports submenu (confirmed present)

Full diff: `git diff main -- smart_mart/templates/sales/thermal_receipt.html smart_mart/blueprints/reports/routes.py smart_mart/templates/reports/dashain_offer.html smart_mart/templates/base.html`

</details>
