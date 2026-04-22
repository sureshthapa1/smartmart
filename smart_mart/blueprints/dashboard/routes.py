"""Dashboard blueprint — enhanced business insights."""

from datetime import date, timedelta

from flask import Blueprint, jsonify, render_template, request
from sqlalchemy import func

from ...extensions import db
from ...models.product import Product
from ...models.sale import Sale, SaleItem
from ...models.expense import Expense
from ...services import alert_engine, cash_flow_manager
from ...services.decorators import login_required

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


def _parse_filter():
    """Parse date filter from request args. Returns (start, end, filter_label)."""
    f = request.args.get("filter", "today")
    today = date.today()
    if f == "today":
        return today, today, "Today"
    elif f == "week":
        return today - timedelta(days=today.weekday()), today, "This Week"
    elif f == "month":
        return today.replace(day=1), today, "This Month"
    elif f == "quarter":
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        return today.replace(month=quarter_start_month, day=1), today, "This Quarter"
    elif f == "year":
        return today.replace(month=1, day=1), today, "This Year"
    elif f == "custom":
        try:
            start = date.fromisoformat(request.args.get("start", str(today)))
            end = date.fromisoformat(request.args.get("end", str(today)))
            return start, end, f"{start} – {end}"
        except ValueError:
            return today, today, "Today"
    return today, today, "Today"


@dashboard_bp.route("/")
@login_required
def index():
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    filter_start, filter_end, filter_label = _parse_filter()
    active_filter = request.args.get("filter", "today")

    # ── Today metrics ─────────────────────────────────────────────────────
    today_sales_amount = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) == today)
    ).scalar() or 0
    today_sales_count = db.session.execute(
        db.select(func.count(Sale.id))
        .where(func.date(Sale.sale_date) == today)
    ).scalar() or 0

    # Today profit = today sales - historical COGS (cost_price at time of sale)
    today_cogs = db.session.execute(
        db.select(
            func.coalesce(
                func.sum(func.coalesce(SaleItem.cost_price, Product.cost_price) * SaleItem.quantity),
                0
            )
        )
        .join(Product, Product.id == SaleItem.product_id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) == today)
    ).scalar() or 0
    today_profit = float(today_sales_amount) - float(today_cogs)

    # ── Weekly / Monthly ──────────────────────────────────────────────────
    weekly_sales_amount = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= week_start)
    ).scalar() or 0
    weekly_sales_count = db.session.execute(
        db.select(func.count(Sale.id))
        .where(func.date(Sale.sale_date) >= week_start)
    ).scalar() or 0

    monthly_sales_amount = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= month_start)
    ).scalar() or 0
    monthly_sales_count = db.session.execute(
        db.select(func.count(Sale.id))
        .where(func.date(Sale.sale_date) >= month_start)
    ).scalar() or 0

    total_sales_count = db.session.execute(db.select(func.count(Sale.id))).scalar() or 0

    # ── Cash Balance = total sales - total expenses ───────────────────────
    total_revenue = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
    ).scalar() or 0
    total_expenses = db.session.execute(
        db.select(func.coalesce(func.sum(Expense.amount), 0))
    ).scalar() or 0
    cash_balance = float(total_revenue) - float(total_expenses)

    # ── Stock value ───────────────────────────────────────────────────────
    stock_value = db.session.execute(
        db.select(func.coalesce(func.sum(Product.cost_price * Product.quantity), 0))
    ).scalar() or 0

    # ── Monthly profit ────────────────────────────────────────────────────
    monthly_profit = cash_flow_manager.profit_loss(month_start, today)["profit"]

    # ── Total products ────────────────────────────────────────────────────
    total_products = db.session.execute(db.select(func.count(Product.id))).scalar() or 0

    # ── Recent sales (last 8) with eager loading ─────────────────────────
    from sqlalchemy.orm import joinedload
    recent_sales = db.session.execute(
        db.select(Sale)
        .options(joinedload(Sale.user), joinedload(Sale.items))
        .order_by(Sale.sale_date.desc())
        .limit(8)
    ).unique().scalars().all()

    # ── Top 5 selling products (this month) ──────────────────────────────
    top5 = db.session.execute(
        db.select(Product, func.sum(SaleItem.quantity).label("qty_sold"))
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= month_start)
        .group_by(Product.id)
        .order_by(func.sum(SaleItem.quantity).desc())
        .limit(5)
    ).all()

    # ── Dead/slow stock (no sales in 30 days) ────────────────────────────
    cutoff_30 = today - timedelta(days=30)
    sold_ids_30 = db.session.execute(
        db.select(SaleItem.product_id.distinct())
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= cutoff_30)
    ).scalars().all()
    dead_stock = db.session.execute(
        db.select(Product)
        .where(Product.id.notin_(sold_ids_30) if sold_ids_30 else db.true())
        .where(Product.quantity > 0)
        .order_by(Product.quantity.desc())
        .limit(5)
    ).scalars().all()

    # ── Period sales (respects active filter) ────────────────────────────
    period_sales_amount = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= filter_start)
        .where(func.date(Sale.sale_date) <= filter_end)
    ).scalar() or 0
    period_sales_count = db.session.execute(
        db.select(func.count(Sale.id))
        .where(func.date(Sale.sale_date) >= filter_start)
        .where(func.date(Sale.sale_date) <= filter_end)
    ).scalar() or 0
    avg_transaction_value = (
        float(period_sales_amount) / period_sales_count
        if period_sales_count > 0 else 0.0
    )

    # ── Reorder alerts (products at or below reorder point) ──────────────
    reorder_alerts_count = db.session.execute(
        db.select(func.count(Product.id))
        .where(Product.quantity <= Product.reorder_point)
        .where(Product.quantity >= 0)
    ).scalar() or 0

    # ── Smart insights ────────────────────────────────────────────────────
    insights = []
    # Sales trend vs last week
    last_week_start = week_start - timedelta(days=7)
    last_week_end = week_start - timedelta(days=1)
    last_week_sales = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= last_week_start)
        .where(func.date(Sale.sale_date) <= last_week_end)
    ).scalar() or 0
    if last_week_sales > 0:
        pct = ((float(weekly_sales_amount) - float(last_week_sales)) / float(last_week_sales)) * 100
        if pct > 0:
            insights.append({"type": "success", "icon": "bi-graph-up-arrow",
                              "text": f"Sales up {pct:.1f}% vs last week"})
        elif pct < -5:
            insights.append({"type": "warning", "icon": "bi-graph-down-arrow",
                              "text": f"Sales down {abs(pct):.1f}% vs last week"})

    alerts = alert_engine.get_all_alerts()
    low_stock = alerts["low_stock"]
    if low_stock:
        insights.append({"type": "warning", "icon": "bi-exclamation-triangle",
                          "text": f"{len(low_stock)} product(s) running low on stock"})
    if top5:
        insights.append({"type": "info", "icon": "bi-trophy",
                          "text": f"Top seller this month: {top5[0].Product.name}"})

    # ── NLG daily summary — always regenerated so it reflects live data ──
    nlg_summary = None
    from flask_login import current_user as cu
    if cu.role == "admin":
        try:
            from ...services.ai_nlg import generate_daily_report
            nlg_data = generate_daily_report()
            nlg_summary = nlg_data.get("narrative", "")
        except Exception:
            pass

    return render_template("dashboard/index.html",
                           # Sales
                           total_sales_count=total_sales_count,
                           today_sales=float(today_sales_amount),
                           today_sales_count=today_sales_count,
                           today_profit=today_profit,
                           cash_balance=cash_balance,
                           weekly_sales=float(weekly_sales_amount),
                           weekly_sales_count=weekly_sales_count,
                           monthly_sales=float(monthly_sales_amount),
                           monthly_sales_count=monthly_sales_count,
                           # Period (active filter)
                           period_sales=float(period_sales_amount),
                           period_sales_count=period_sales_count,
                           avg_transaction_value=avg_transaction_value,
                           # Other
                           total_products=total_products,
                           stock_value=float(stock_value),
                           monthly_profit=float(monthly_profit),
                           # Widgets
                           recent_sales=recent_sales,
                           top5=top5,
                           dead_stock=dead_stock,
                           insights=insights,
                           # Alerts
                           low_stock=low_stock,
                           reorder_alerts_count=reorder_alerts_count,
                           alert_counts={
                               "low_stock": len(alerts["low_stock"]),
                               "expiry": len(alerts["expiry"]),
                               "high_demand": len(alerts["high_demand"]),
                               "reorder": reorder_alerts_count,
                           },
                           # Filter
                           active_filter=active_filter,
                           filter_label=filter_label,
                           filter_start=str(filter_start),
                           filter_end=str(filter_end),
                           nlg_summary=nlg_summary,
                           )
