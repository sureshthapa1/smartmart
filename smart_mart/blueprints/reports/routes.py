"""Reports blueprint — cash flow, analytics, stock analysis, and exports."""

from __future__ import annotations

from datetime import date, timedelta

from flask import Blueprint, Response, flash, render_template, request
from flask_login import current_user

from ...services import cash_flow_manager, report_engine, exporter
from ...services.decorators import login_required, admin_required

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


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
@admin_required
def cash_flow():
    start, end, start_raw, end_raw = _get_date_range()
    data = cash_flow_manager.profit_loss(start, end)
    balance = cash_flow_manager.daily_balance(date.today())
    return render_template("reports/cash_flow.html", data=data, balance=balance,
                           start_date=start_raw, end_date=end_raw)


@reports_bp.route("/sales")
@admin_required
def sales_report():
    start, end, start_raw, end_raw = _get_date_range()
    rows = report_engine.sales_report(start, end)
    return render_template("reports/sales_report.html", rows=rows,
                           start_date=start_raw, end_date=end_raw)


@reports_bp.route("/top-products")
@admin_required
def top_products():
    start, end, start_raw, end_raw = _get_date_range()
    rows = report_engine.top_products(start, end)
    return render_template("reports/top_products.html", rows=rows,
                           start_date=start_raw, end_date=end_raw, title="Top Selling Products")


@reports_bp.route("/least-products")
@admin_required
def least_products():
    start, end, start_raw, end_raw = _get_date_range()
    rows = report_engine.least_products(start, end)
    return render_template("reports/top_products.html", rows=rows,
                           start_date=start_raw, end_date=end_raw, title="Least Selling Products")


@reports_bp.route("/dead-stock")
@admin_required
def dead_stock():
    products = report_engine.dead_stock()
    return render_template("reports/dead_stock.html", products=products)


@reports_bp.route("/profitability")
@admin_required
def profitability():
    start, end, start_raw, end_raw = _get_date_range()
    rows = report_engine.profitability_analysis(start, end)
    return render_template("reports/profitability.html", rows=rows,
                           start_date=start_raw, end_date=end_raw)


@reports_bp.route("/category-performance")
@admin_required
def category_performance():
    start, end, start_raw, end_raw = _get_date_range()
    rows = report_engine.category_performance(start, end)
    return render_template("reports/category_performance.html", rows=rows,
                           start_date=start_raw, end_date=end_raw)


@reports_bp.route("/inventory-valuation")
@admin_required
def inventory_valuation():
    data = report_engine.inventory_valuation()
    return render_template("reports/inventory_valuation.html", data=data)


@reports_bp.route("/stock-analysis")
@admin_required
def stock_analysis():
    start, end, start_raw, end_raw = _get_date_range()
    rows = report_engine.opening_closing_stock(start, end)
    return render_template("reports/stock_analysis.html", rows=rows,
                           start_date=start_raw, end_date=end_raw)


# --- Export endpoints ---

@reports_bp.route("/profitability/export-csv")
@admin_required
def export_profitability_csv():
    start, end, _, _ = _get_date_range()
    rows = report_engine.profitability_analysis(start, end)
    data = [{"Product": r["product"].name, "SKU": r["product"].sku,
             "Qty Sold": r["qty_sold"], "Revenue": r["revenue"],
             "Profit": r["profit"], "Margin %": round(r["margin"], 2)} for r in rows]
    csv_str = exporter.export_report_csv(data, ["Product", "SKU", "Qty Sold", "Revenue", "Profit", "Margin %"])
    return Response(csv_str, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=profitability.csv"})


@reports_bp.route("/sales/export-csv")
@login_required
def export_sales_csv():
    start, end, _, _ = _get_date_range()
    rows = report_engine.sales_report(start, end)
    csv_str = exporter.export_report_csv(rows, ["date", "total"])
    return Response(csv_str, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=sales_report.csv"})
