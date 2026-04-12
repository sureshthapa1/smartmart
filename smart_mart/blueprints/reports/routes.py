"""Reports blueprint — cash flow, analytics, stock analysis, and exports."""

from __future__ import annotations

from datetime import date, timedelta

from flask import Blueprint, Response, flash, render_template, request
from flask_login import current_user

from ...services import cash_flow_manager, report_engine, exporter
from ...services.decorators import login_required, admin_required

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")

def _require_perm(perm: str):
    """Abort 403 if staff user lacks the given permission."""
    from flask import abort
    from flask_login import current_user as _cu
    if _cu.role != "admin":
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(_cu.id)
        if not getattr(p, perm, False):
            abort(403)




def _get_risk_map() -> dict:
    """Return {customer_name_lower: risk_data} for credit ledger badges."""
    try:
        from ...models.customer_risk_score import CustomerRiskScore
        from ...extensions import db
        from ...services.credit_risk_service import TIER_LABELS
        rows = db.session.execute(db.select(CustomerRiskScore)).scalars().all()
        result = {}
        for row in rows:
            label, color = TIER_LABELS[row.effective_tier]
            result[row.customer_name.lower()] = {
                "risk_label": label,
                "risk_color": color,
                "has_override": row.has_override,
            }
        return result
    except Exception:
        return {}
def _get_date_range():
    start_raw = request.args.get("start_date", "")
    end_raw = request.args.get("end_date", "")
    try:
        start = date.fromisoformat(start_raw) if start_raw else date.today() - timedelta(days=30)
    except ValueError:
        start = date.today() - timedelta(days=30)
    try:
        end = date.fromisoformat(end_raw) if end_raw else date.today()
    except ValueError:
        end = date.today()
    return start, end, start_raw, end_raw


@reports_bp.route("/cash-flow")
@login_required
def cash_flow():
    _require_perm("can_view_reports")
    from sqlalchemy import func, and_
    from ...extensions import db
    from ...models.sale import Sale, SaleItem
    from ...models.product import Product
    from ...models.expense import Expense

    start, end, start_raw, end_raw = _get_date_range()
    data = cash_flow_manager.profit_loss(start, end)
    balance = cash_flow_manager.daily_balance(date.today())

    # ── Revenue by Payment Mode ───────────────────────────────────────────
    pm_rows = db.session.execute(
        db.select(
            Sale.payment_mode,
            func.coalesce(func.sum(Sale.total_amount), 0).label("revenue"),
            func.count(Sale.id).label("txn_count"),
        )
        .where(and_(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end))
        .group_by(Sale.payment_mode)
        .order_by(func.sum(Sale.total_amount).desc())
    ).all()

    pm_labels = {"cash": "💵 Cash", "qr": "📱 QR/Digital", "card": "💳 Card",
                 "credit": "📋 Credit/Udharo", "other": "🔄 Other", None: "💵 Cash"}
    payment_mode_data = [{
        "mode": pm_labels.get(r.payment_mode, str(r.payment_mode or "Cash")),
        "raw_mode": r.payment_mode or "cash",
        "revenue": float(r.revenue),
        "txn_count": r.txn_count,
    } for r in pm_rows]

    total_pm_revenue = sum(r["revenue"] for r in payment_mode_data)
    for r in payment_mode_data:
        r["pct"] = round((r["revenue"] / total_pm_revenue * 100), 1) if total_pm_revenue else 0

    # ── Revenue by Category ───────────────────────────────────────────────
    cat_rows = db.session.execute(
        db.select(
            Product.category,
            func.coalesce(func.sum(SaleItem.subtotal), 0).label("revenue"),
            func.sum(SaleItem.quantity).label("qty_sold"),
            func.count(SaleItem.id.distinct()).label("txn_count"),
        )
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(and_(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end))
        .group_by(Product.category)
        .order_by(func.sum(SaleItem.subtotal).desc())
    ).all()

    category_data = [{
        "category": r.category or "Uncategorized",
        "revenue": float(r.revenue),
        "qty_sold": r.qty_sold,
        "txn_count": r.txn_count,
    } for r in cat_rows]

    total_cat_revenue = sum(r["revenue"] for r in category_data)
    for r in category_data:
        r["pct"] = round((r["revenue"] / total_cat_revenue * 100), 1) if total_cat_revenue else 0

    # ── Expense breakdown by type ─────────────────────────────────────────
    exp_rows = db.session.execute(
        db.select(
            Expense.expense_type,
            func.coalesce(func.sum(Expense.amount), 0).label("total"),
            func.count(Expense.id).label("count"),
        )
        .where(and_(Expense.expense_date >= start, Expense.expense_date <= end))
        .group_by(Expense.expense_type)
        .order_by(func.sum(Expense.amount).desc())
    ).all()

    expense_breakdown = [{
        "type": r.expense_type or "Other",
        "total": float(r.total),
        "count": r.count,
    } for r in exp_rows]

    return render_template("reports/cash_flow.html",
                           data=data, balance=balance,
                           start_date=start_raw, end_date=end_raw,
                           payment_mode_data=payment_mode_data,
                           category_data=category_data,
                           expense_breakdown=expense_breakdown,
                           total_pm_revenue=total_pm_revenue,
                           total_cat_revenue=total_cat_revenue)


@reports_bp.route("/sales")
@login_required
def sales_report():
    _require_perm("can_view_sales_report")
    start, end, start_raw, end_raw = _get_date_range()
    period = request.args.get("period", "daily")
    summary = report_engine.sales_summary(start, end)
    by_period = report_engine.sales_by_period(start, end, period)
    top = report_engine.top_products(start, end, n=10)
    least = report_engine.least_products(start, end, n=10)
    product_wise = report_engine.product_wise_sales(start, end)
    staff = report_engine.staff_sales_report(start, end)
    hourly = report_engine.hourly_sales(start, end)
    return render_template("reports/sales_report.html",
                           summary=summary, by_period=by_period,
                           top=top, least=least, product_wise=product_wise,
                           staff=staff, hourly=hourly,
                           period=period, start_date=start_raw, end_date=end_raw)


@reports_bp.route("/top-products")
@login_required
def top_products():
    _require_perm("can_view_sales_report")
    start, end, start_raw, end_raw = _get_date_range()
    rows = report_engine.top_products(start, end)
    return render_template("reports/top_products.html", rows=rows,
                           start_date=start_raw, end_date=end_raw, title="Top Selling Products")


@reports_bp.route("/least-products")
@login_required
def least_products():
    _require_perm("can_view_sales_report")
    start, end, start_raw, end_raw = _get_date_range()
    rows = report_engine.least_products(start, end)
    return render_template("reports/top_products.html", rows=rows,
                           start_date=start_raw, end_date=end_raw, title="Least Selling Products")


@reports_bp.route("/dead-stock")
@login_required
def dead_stock():
    _require_perm("can_view_stock_report")
    products = report_engine.dead_stock()
    return render_template("reports/dead_stock.html", products=products)


@reports_bp.route("/profitability")
@login_required
def profitability():
    _require_perm("can_view_profit_report")
    start, end, start_raw, end_raw = _get_date_range()
    rows = report_engine.profitability_analysis(start, end)
    return render_template("reports/profitability.html", rows=rows,
                           start_date=start_raw, end_date=end_raw)


@reports_bp.route("/category-performance")
@login_required
def category_performance():
    _require_perm("can_view_reports")
    start, end, start_raw, end_raw = _get_date_range()
    rows = report_engine.category_performance(start, end)
    return render_template("reports/category_performance.html", rows=rows,
                           start_date=start_raw, end_date=end_raw)


@reports_bp.route("/inventory-valuation")
@login_required
def inventory_valuation():
    _require_perm("can_view_stock_report")
    data = report_engine.inventory_valuation()
    return render_template("reports/inventory_valuation.html", data=data)


@reports_bp.route("/stock-analysis")
@login_required
def stock_analysis():
    _require_perm("can_view_stock_report")
    start, end, start_raw, end_raw = _get_date_range()
    rows = report_engine.opening_closing_stock(start, end)
    return render_template("reports/stock_analysis.html", rows=rows,
                           start_date=start_raw, end_date=end_raw)


# --- Export endpoints ---

@reports_bp.route("/profitability/export-csv")
@login_required
def export_profitability_csv():
    _require_perm("can_view_profit_report")
    start, end, _, _ = _get_date_range()
    rows = report_engine.profitability_analysis(start, end)
    data = [{"Product": r["product"].name, "SKU": r["product"].sku,
             "Qty Sold": r["qty_sold"], "Revenue": r["revenue"],
             "Profit": r["profit"], "Margin %": round(r["margin"], 2)} for r in rows]
    csv_str = exporter.export_report_csv(data, ["Product", "SKU", "Qty Sold", "Revenue", "Profit", "Margin %"])
    return Response(csv_str, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=profitability.csv"})


@reports_bp.route("/staff-efficiency")
@login_required
def staff_efficiency():
    _require_perm("can_view_reports")
    start, end, start_raw, end_raw = _get_date_range()
    data = report_engine.staff_efficiency_report(start, end)
    return render_template("reports/staff_efficiency.html",
                           data=data, start_date=start_raw, end_date=end_raw)


@reports_bp.route("/sales/export-csv")
@login_required
def export_sales_csv():
    from ...extensions import db
    from ...models.sale import Sale, SaleItem
    from sqlalchemy import and_, func
    import csv, io
    start, end, _, _ = _get_date_range()
    sales = db.session.execute(
        db.select(Sale)
        .where(and_(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end))
        .order_by(Sale.sale_date.desc())
    ).scalars().all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Invoice No", "Customer", "Payment Mode", "Items", "Subtotal", "Discount", "Total"])
    for s in sales:
        subtotal = sum(float(i.subtotal) for i in s.items)
        writer.writerow([
            s.sale_date.strftime("%Y-%m-%d %H:%M") if s.sale_date else "",
            s.invoice_number or f"INV-{s.id:05d}",
            s.customer_name or "Walk-in",
            s.payment_mode or "cash",
            len(s.items),
            round(subtotal, 2),
            float(s.discount_amount or 0),
            float(s.total_amount),
        ])
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=sales_report.csv"})


@reports_bp.route("/top-products/export-csv")
@login_required
def export_top_products_csv():
    _require_perm("can_view_sales_report")
    start, end, _, _ = _get_date_range()
    rows = report_engine.top_products(start, end)
    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Rank", "Product", "SKU", "Category", "Qty Sold"])
    for i, r in enumerate(rows, 1):
        writer.writerow([i, r["product"].name, r["product"].sku,
                         r["product"].category or "", r["qty_sold"]])
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=top_products.csv"})


@reports_bp.route("/category-performance/export-csv")
@login_required
def export_category_csv():
    _require_perm("can_view_reports")
    start, end, _, _ = _get_date_range()
    rows = report_engine.category_performance(start, end)
    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Category", "Revenue", "Qty Sold", "Transactions", "Avg Order"])
    for r in rows:
        writer.writerow([r.get("category", ""), r.get("revenue", 0),
                         r.get("qty_sold", 0), r.get("txn_count", 0), r.get("avg_order", 0)])
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=category_performance.csv"})


@reports_bp.route("/inventory-valuation/export-csv")
@login_required
def export_inventory_csv():
    _require_perm("can_view_stock_report")
    data = report_engine.inventory_valuation()
    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Product", "SKU", "Category", "Qty", "Cost Price", "Selling Price", "Stock Value"])
    for r in data.get("products", []):
        p = r.get("product")
        if p:
            writer.writerow([p.name, p.sku, p.category or "", p.quantity,
                             float(p.cost_price), float(p.selling_price),
                             float(p.cost_price) * p.quantity])
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=inventory_valuation.csv"})


@reports_bp.route("/staff-efficiency/export-csv")
@login_required
def export_staff_csv():
    _require_perm("can_view_reports")
    start, end, _, _ = _get_date_range()
    data = report_engine.staff_efficiency_report(start, end)
    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Staff", "Role", "Transactions", "Items Sold", "Revenue", "Avg Sale"])
    for r in data.get("staff", []):
        writer.writerow([r.get("username", ""), r.get("role", ""),
                         r.get("transactions", 0), r.get("items_sold", 0),
                         r.get("revenue", 0), r.get("avg_sale", 0)])
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=staff_efficiency.csv"})


@reports_bp.route("/credit-udharo")
@login_required
def credit_udharo():
    _require_perm("can_view_credit_report")
    from ...extensions import db
    from ...models.sale import Sale

    today = date.today()
    status_filter = request.args.get("status", "all")  # all | pending | overdue | collected

    q = db.select(Sale).where(Sale.payment_mode == "credit").order_by(Sale.sale_date.desc())
    all_credit = db.session.execute(q).scalars().all()

    # Annotate each sale
    records = []
    for s in all_credit:
        if s.credit_collected:
            status = "collected"
            status_color = "success"
        elif s.credit_due_date and s.credit_due_date < today:
            status = "overdue"
            status_color = "danger"
        elif s.credit_due_date and s.credit_due_date <= today + timedelta(days=3):
            status = "due_soon"
            status_color = "warning"
        else:
            status = "pending"
            status_color = "secondary"

        records.append({
            "sale": s,
            "status": status,
            "status_color": status_color,
            "days_overdue": (today - s.credit_due_date).days if (s.credit_due_date and s.credit_due_date < today and not s.credit_collected) else 0,
        })

    # Apply filter
    if status_filter != "all":
        records = [r for r in records if r["status"] == status_filter]

    # Summary stats (always from full list)
    total_credit = sum(float(r["sale"].total_amount) for r in records)
    pending_records = [r for r in records if r["status"] in ("pending", "due_soon", "overdue")]
    overdue_records = [r for r in records if r["status"] == "overdue"]
    collected_records = [r for r in records if r["status"] == "collected"]

    summary = {
        "total_count": len(all_credit),
        "total_amount": sum(float(s.total_amount) for s in all_credit),
        "pending_amount": sum(float(s.total_amount) for s in all_credit if not s.credit_collected),
        "overdue_count": sum(1 for s in all_credit
                             if not s.credit_collected and s.credit_due_date and s.credit_due_date < today),
        "collected_amount": sum(float(s.total_amount) for s in all_credit if s.credit_collected),
    }

    return render_template("reports/credit_udharo.html",
                           records=records,
                           summary=summary,
                           status_filter=status_filter,
                           today=today,
                           risk_scores=_get_risk_map())


@reports_bp.route("/credit-udharo/<int:sale_id>/mark-collected", methods=["POST"])
@login_required
def mark_credit_collected(sale_id):
    _require_perm("can_view_credit_report")
    from ...extensions import db
    from ...models.sale import Sale
    sale = db.get_or_404(Sale, sale_id)
    sale.credit_collected = True
    db.session.commit()
    flash(f"Credit for {sale.customer_name or 'customer'} marked as collected.", "success")
    from flask import redirect, url_for
    return redirect(url_for("reports.credit_udharo"))


@reports_bp.route("/credit-udharo/<int:sale_id>/set-due-date", methods=["POST"])
@login_required
def set_credit_due_date(sale_id):
    _require_perm("can_view_credit_report")
    from ...extensions import db
    from ...models.sale import Sale
    from flask import redirect, url_for
    sale = db.get_or_404(Sale, sale_id)
    due_raw = request.form.get("due_date", "")
    try:
        sale.credit_due_date = date.fromisoformat(due_raw)
        db.session.commit()
        flash("Collection date updated.", "success")
    except ValueError:
        flash("Invalid date.", "danger")
    return redirect(url_for("reports.credit_udharo"))


# ── Excel Export Endpoints ────────────────────────────────────────────────────

@reports_bp.route("/sales/export-excel")
@login_required
def export_sales_excel():
    _require_perm("can_view_sales_report")
    from flask import Response
    start, end, _, _ = _get_date_range()
    rows = report_engine.product_wise_sales(start, end)
    data = [{"Product": r["product"].name, "SKU": r["product"].sku,
             "Qty Sold": r["qty_sold"], "Revenue (NPR)": float(r["revenue"]),
             "Profit (NPR)": float(r.get("profit", 0))} for r in rows]
    xlsx = exporter.export_report_excel(data, f"Sales Report {start} to {end}",
                                        ["Product", "SKU", "Qty Sold", "Revenue (NPR)", "Profit (NPR)"])
    return Response(xlsx, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename=sales_{start}_{end}.xlsx"})


@reports_bp.route("/inventory-valuation/export-excel")
@login_required
def export_inventory_excel():
    _require_perm("can_view_stock_report")
    from flask import Response
    result = report_engine.inventory_valuation()
    items = result.get("items", []) if isinstance(result, dict) else result
    data = [{"Product": r["product"].name, "SKU": r["product"].sku,
             "Category": r["product"].category or "",
             "Qty": r["product"].quantity,
             "Cost Price": float(r["product"].cost_price),
             "Valuation (NPR)": float(r["valuation"])} for r in items]
    xlsx = exporter.export_report_excel(data, "Inventory Valuation",
                                        ["Product", "SKU", "Category", "Qty", "Cost Price", "Valuation (NPR)"])
    return Response(xlsx, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=inventory_valuation.xlsx"})


@reports_bp.route("/profitability/export-excel")
@login_required
def export_profitability_excel():
    _require_perm("can_view_profit_report")
    from flask import Response
    start, end, _, _ = _get_date_range()
    rows = report_engine.profitability_analysis(start, end)
    data = [{"Product": r.get("name", ""), "Revenue": float(r.get("revenue", 0)),
             "COGS": float(r.get("cogs", 0)), "Profit": float(r.get("profit", 0)),
             "Margin %": float(r.get("margin_pct", 0))} for r in rows]
    xlsx = exporter.export_report_excel(data, f"Profitability {start} to {end}",
                                        ["Product", "Revenue", "COGS", "Profit", "Margin %"])
    return Response(xlsx, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=profitability.xlsx"})


@reports_bp.route("/shift-sales")
@login_required
def shift_sales():
    _require_perm("can_view_reports")
    from ...models.shift import Shift
    from ...models.sale import Sale, SaleItem
    from ...models.user import User
    from ...extensions import db as _db
    from sqlalchemy import func as _f

    shifts = _db.session.execute(
        _db.select(Shift).order_by(Shift.started_at.desc()).limit(30)
    ).scalars().all()

    shift_data = []
    for shift in shifts:
        if not shift.started_at:
            continue
        import datetime as _dt
        end = shift.ended_at or _dt.datetime.now(_dt.timezone.utc)
        sales = _db.session.execute(
            _db.select(Sale)
            .where(Sale.user_id == shift.user_id)
            .where(Sale.sale_date >= shift.started_at)
            .where(Sale.sale_date <= end)
        ).scalars().all()
        revenue = sum(float(s.total_amount) for s in sales)
        items_sold = sum(
            _db.session.execute(
                _db.select(_f.coalesce(_f.sum(SaleItem.quantity), 0))
                .where(SaleItem.sale_id == s.id)
            ).scalar() or 0
            for s in sales
        )
        user = _db.session.get(User, shift.user_id)
        shift_data.append({
            "shift": shift,
            "user": user,
            "sales_count": len(sales),
            "revenue": revenue,
            "items_sold": items_sold,
            "duration_hrs": round((end - shift.started_at).total_seconds() / 3600, 1) if shift.started_at else 0,
        })

    return render_template("reports/shift_sales.html", shift_data=shift_data)


@reports_bp.route("/discount-analysis")
@login_required
def discount_analysis():
    _require_perm("can_view_reports")
    start, end, start_raw, end_raw = _get_date_range()
    data = report_engine.discount_analysis(start, end)
    return render_template("reports/discount_analysis.html",
                           data=data, start_date=start_raw, end_date=end_raw)


@reports_bp.route("/customer-analysis")
@login_required
def customer_analysis():
    _require_perm("can_view_reports")
    start, end, start_raw, end_raw = _get_date_range()
    data = report_engine.customer_analysis(start, end)
    return render_template("reports/customer_analysis.html",
                           data=data, start_date=start_raw, end_date=end_raw)
