"""Dashboard blueprint — enhanced business insights."""

from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user as cu
from sqlalchemy import func

from ...extensions import db
from ...models.product import Product
from ...models.sale import Sale, SaleItem
from ...models.expense import Expense
from ...services import alert_engine, cash_flow_manager
from ...services.decorators import login_required
from ...services.cache_service import get as _cache_get, set as _cache_set

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


def _to_dt(d: date) -> datetime:
    """Convert a date to a UTC-aware datetime at midnight for index-friendly comparisons."""
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _parse_filter():
    """Parse date filter from request args. Returns (start_dt, end_dt, filter_label) as datetimes."""
    f = request.args.get("filter", "today")
    today = date.today()
    if f == "today":
        return _to_dt(today), _to_dt(today), "Today"
    elif f == "week":
        return _to_dt(today - timedelta(days=today.weekday())), _to_dt(today), "This Week"
    elif f == "month":
        return _to_dt(today.replace(day=1)), _to_dt(today), "This Month"
    elif f == "quarter":
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        return _to_dt(today.replace(month=quarter_start_month, day=1)), _to_dt(today), "This Quarter"
    elif f == "year":
        return _to_dt(today.replace(month=1, day=1)), _to_dt(today), "This Year"
    elif f == "custom":
        try:
            start = date.fromisoformat(request.args.get("start", str(today)))
            end = date.fromisoformat(request.args.get("end", str(today)))
            return _to_dt(start), _to_dt(end), f"{start} – {end}"
        except ValueError:
            return _to_dt(today), _to_dt(today), "Today"
    return _to_dt(today), _to_dt(today), "Today"


@dashboard_bp.route("/")
@login_required
def index():
    today_date  = date.today()
    today       = datetime(today_date.year, today_date.month, today_date.day, tzinfo=timezone.utc)
    today_end   = datetime(today_date.year, today_date.month, today_date.day, 23, 59, 59, 999999, tzinfo=timezone.utc)
    week_start  = today - timedelta(days=today_date.weekday())
    month_start = today.replace(day=1)
    filter_start, filter_end, filter_label = _parse_filter()
    active_filter = request.args.get("filter", "today")
    low_stock_alerts = []
    try:
        from ...utils.low_stock import get_low_stock_alerts
        low_stock_alerts = get_low_stock_alerts()
    except Exception:
        pass

    # ── Combined Sales Aggregation — 1 query replaces 7 separate round-trips ─
    # Uses CASE/WHEN to compute today / week / month / total in a single pass.
    # Uses datetime range comparisons (>= / <=) instead of func.date() wrappers
    # so PostgreSQL can use the ix_sale_date index on Sale.sale_date (DateTime).
    # func.date(Sale.sale_date) prevents index use even when the column is indexed.
    from sqlalchemy import case, literal_column
    agg_row = db.session.execute(
        db.select(
            func.coalesce(func.sum(
                case((Sale.sale_date.between(today, today_end), Sale.total_amount), else_=0)
            ), 0).label("today_amount"),
            func.coalesce(func.sum(
                case((Sale.sale_date.between(today, today_end), 1), else_=0)
            ), 0).label("today_count"),
            func.coalesce(func.sum(
                case((Sale.sale_date >= week_start, Sale.total_amount), else_=0)
            ), 0).label("weekly_amount"),
            func.coalesce(func.sum(
                case((Sale.sale_date >= week_start, 1), else_=0)
            ), 0).label("weekly_count"),
            func.coalesce(func.sum(
                case((Sale.sale_date >= month_start, Sale.total_amount), else_=0)
            ), 0).label("monthly_amount"),
            func.coalesce(func.sum(
                case((Sale.sale_date >= month_start, 1), else_=0)
            ), 0).label("monthly_count"),
            func.count(Sale.id).label("total_count"),
            func.coalesce(func.sum(Sale.total_amount), 0).label("total_revenue"),
        )
    ).one()

    today_sales_amount  = float(agg_row.today_amount)
    today_sales_count   = int(agg_row.today_count)
    weekly_sales_amount = float(agg_row.weekly_amount)
    weekly_sales_count  = int(agg_row.weekly_count)
    monthly_sales_amount = float(agg_row.monthly_amount)
    monthly_sales_count  = int(agg_row.monthly_count)
    total_sales_count   = int(agg_row.total_count)
    total_revenue       = float(agg_row.total_revenue)

    # ── Today COGS (separate join query — can't combine with above) ────────
    today_cogs = db.session.execute(
        db.select(
            func.coalesce(
                func.sum(func.coalesce(SaleItem.cost_price, Product.cost_price) * SaleItem.quantity),
                0
            )
        )
        .join(Product, Product.id == SaleItem.product_id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(Sale.sale_date.between(today, today_end))
    ).scalar() or 0
    today_profit = float(today_sales_amount) - float(today_cogs)

    # ── Cash Balance: total revenue - total expenses (2 queries → 1) ──────
    total_expenses = db.session.execute(
        db.select(func.coalesce(func.sum(Expense.amount), 0))
    ).scalar() or 0
    cash_balance = total_revenue - float(total_expenses)

    # ── Stock value ───────────────────────────────────────────────────────
    stock_value = db.session.execute(
        db.select(func.coalesce(func.sum(Product.cost_price * Product.quantity), 0))
    ).scalar() or 0

    # ── Monthly profit ────────────────────────────────────────────────────
    monthly_profit = cash_flow_manager.profit_loss(month_start, today)["profit"]
    waste_cost_month = 0.0
    try:
        from ...models.waste_record import WasteRecord
        waste_cost_month = float(db.session.execute(
            db.select(func.coalesce(func.sum(WasteRecord.cost_value), 0))
            .where(WasteRecord.created_at >= month_start)
        ).scalar() or 0)
    except Exception:
        pass

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
        .where(Sale.sale_date >= month_start)
        .group_by(Product.id)
        .order_by(func.sum(SaleItem.quantity).desc())
        .limit(5)
    ).all()

    # ── Dead/slow stock (no sales in 30 days) ────────────────────────────
    cutoff_30 = today - timedelta(days=30)
    sold_ids_30 = db.session.execute(
        db.select(SaleItem.product_id.distinct())
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(Sale.sale_date >= cutoff_30)
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
        .where(Sale.sale_date >= filter_start)
        .where(Sale.sale_date <= filter_end)
    ).scalar() or 0
    period_sales_count = db.session.execute(
        db.select(func.count(Sale.id))
        .where(Sale.sale_date >= filter_start)
        .where(Sale.sale_date <= filter_end)
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
        .where(Sale.sale_date >= last_week_start)
        .where(Sale.sale_date <= last_week_end)
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

    # ── Cash session balance (open session for current user) ─────────────
    cash_session_balance = None
    cash_session_open = False
    try:
        from ...models.operations import CashSession
        open_session = db.session.execute(
            db.select(CashSession)
            .where(CashSession.user_id == cu.id, CashSession.closed_at.is_(None))
            .order_by(CashSession.opened_at.desc())
        ).scalar_one_or_none()
        if open_session:
            cash_session_open = True
            cash_session_balance = float(open_session.opening_balance or 0) + float(today_sales_amount)
    except Exception:
        pass

    # ── Pending credits (outstanding credit sales) ────────────────────────
    pending_credits_total = 0.0
    pending_credits_count = 0
    try:
        from ...models.sale import Sale as _Sale
        result = db.session.execute(
            db.select(
                func.count(_Sale.id).label("cnt"),
                func.coalesce(func.sum(_Sale.total_amount), 0).label("total"),
            )
            .where(_Sale.payment_mode == "credit", _Sale.credit_collected == False)
        ).one()
        pending_credits_count = result.cnt or 0
        pending_credits_total = float(result.total or 0)
    except Exception:
        pass

    # ── Pending online orders (awaiting fulfillment) ──────────────────────
    pending_orders_count = 0
    try:
        from ...models.online_order import OnlineOrder as _OnlineOrder
        pending_orders_count = db.session.execute(
            db.select(func.count(_OnlineOrder.id))
            .where(_OnlineOrder.status == "pending")
        ).scalar() or 0
    except Exception:
        pass
    # NLG summary + advisor actions — cached per-day to avoid recomputing on every load
    nlg_summary = None
    advisor_actions = []
    if cu.role == "admin":
        import datetime as _dt
        _nlg_key = f"dashboard_nlg:{_dt.date.today()}"
        _cached_nlg = _cache_get(_nlg_key)
        if _cached_nlg is not None:
            nlg_summary = _cached_nlg
        else:
            try:
                from ...services.ai_nlg import generate_daily_report
                nlg_data = generate_daily_report()
                nlg_summary = nlg_data.get("narrative", "")
                _cache_set(_nlg_key, nlg_summary, ttl=3600)  # cache 1 hour
            except Exception:
                pass

        _adv_key = f"dashboard_advisor:{_dt.date.today()}"
        _cached_adv = _cache_get(_adv_key)
        if _cached_adv is not None:
            advisor_actions = _cached_adv
        else:
            try:
                from ...services.ai_business_advisor import product_action_recommendations
                all_actions = product_action_recommendations()
                advisor_actions = [a for a in all_actions if a.get("priority", 9) <= 2][:3]
                _cache_set(_adv_key, advisor_actions, ttl=3600)  # cache 1 hour
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
                           advisor_actions=advisor_actions,
                           cash_session_open=cash_session_open,
                           cash_session_balance=cash_session_balance,
                           pending_credits_total=pending_credits_total,
                           pending_credits_count=pending_credits_count,
                           pending_orders_count=pending_orders_count,
                           low_stock_alerts=low_stock_alerts,
                           waste_cost_month=waste_cost_month,
                           )
