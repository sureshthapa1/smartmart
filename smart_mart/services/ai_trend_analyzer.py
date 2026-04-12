"""AI Module 1: Trend & Fast-Moving Product Analyzer

Identifies fastest/slowest selling products, seasonal patterns,
and generates dashboard-ready JSON insights.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import and_, func

from ..extensions import db
from ..models.product import Product
from ..models.sale import Sale, SaleItem
from .db_compat import date_format_year_month, date_format_dow


def _week_label(d: date) -> str:
    return f"{d.year}-W{d.isocalendar()[1]:02d}"


def _month_label(d: date) -> str:
    return d.strftime("%Y-%m")


def fast_moving_products(period: str = "weekly", top_n: int = 10) -> dict:
    """Identify fastest-selling products.

    Args:
        period: 'weekly' | 'monthly'
        top_n: number of products to return

    Returns dashboard-ready JSON dict.
    """
    today = date.today()
    if period == "weekly":
        start = today - timedelta(days=today.weekday())
        label = f"Week of {start}"
    else:
        start = today.replace(day=1)
        label = today.strftime("%B %Y")

    rows = db.session.execute(
        db.select(
            Product,
            func.sum(SaleItem.quantity).label("qty_sold"),
            func.sum(SaleItem.subtotal).label("revenue"),
            func.count(SaleItem.id.distinct()).label("order_count"),
        )
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= start)
        .group_by(Product.id)
        .order_by(func.sum(SaleItem.quantity).desc())
        .limit(top_n)
    ).all()

    products = []
    for r in rows:
        p = r.Product
        velocity = round(r.qty_sold / max((date.today() - start).days, 1), 2)
        products.append({
            "id": p.id,
            "name": p.name,
            "sku": p.sku,
            "category": p.category,
            "qty_sold": r.qty_sold,
            "revenue": float(r.revenue),
            "order_count": r.order_count,
            "velocity_per_day": velocity,
            "current_stock": p.quantity,
            "days_of_stock": round(p.quantity / velocity, 1) if velocity > 0 else 999,
        })

    return {
        "period": period,
        "label": label,
        "start_date": str(start),
        "products": products,
        "total_products_analyzed": len(products),
    }


def dead_stock_analysis(days: int = 30) -> dict:
    """Identify dead stock with financial impact analysis."""
    cutoff = date.today() - timedelta(days=days)
    sold_ids = db.session.execute(
        db.select(SaleItem.product_id.distinct())
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= cutoff)
    ).scalars().all()

    products = db.session.execute(
        db.select(Product)
        .where(Product.id.notin_(sold_ids) if sold_ids else db.true())
        .where(Product.quantity > 0)
        .order_by((Product.cost_price * Product.quantity).desc())
    ).scalars().all()

    total_dead_value = sum(float(p.cost_price) * p.quantity for p in products)
    items = []
    for p in products:
        last_sale = db.session.execute(
            db.select(func.max(Sale.sale_date))
            .join(SaleItem, SaleItem.sale_id == Sale.id)
            .where(SaleItem.product_id == p.id)
        ).scalar()
        days_idle = (date.today() - last_sale.date()).days if last_sale else None
        items.append({
            "id": p.id,
            "name": p.name,
            "sku": p.sku,
            "category": p.category,
            "quantity": p.quantity,
            "cost_price": float(p.cost_price),
            "stock_value": float(p.cost_price) * p.quantity,
            "last_sale_date": str(last_sale.date()) if last_sale else None,
            "days_idle": days_idle,
            "action": "clearance_sale" if float(p.cost_price) * p.quantity > 1000 else "remove",
        })

    return {
        "threshold_days": days,
        "total_dead_products": len(items),
        "total_dead_stock_value": round(total_dead_value, 2),
        "items": items,
        "recommendation": (
            f"NPR {total_dead_value:,.0f} tied up in dead stock. "
            "Consider clearance sales or supplier returns."
        ) if total_dead_value > 0 else "No dead stock detected.",
    }


def seasonal_demand_patterns(product_id: int = None) -> dict:
    """Analyze seasonal demand patterns by month and day-of-week."""
    # Monthly pattern (last 12 months)
    monthly = db.session.execute(
        db.select(
            date_format_year_month(Sale.sale_date).label("month"),
            func.sum(SaleItem.quantity).label("qty"),
            func.sum(SaleItem.subtotal).label("revenue"),
        )
        .join(SaleItem, SaleItem.sale_id == Sale.id)
        .where(*(
            [SaleItem.product_id == product_id] if product_id else []
        ))
        .group_by(date_format_year_month(Sale.sale_date))
        .order_by(date_format_year_month(Sale.sale_date))
    ).all()

    # Day-of-week pattern
    dow = db.session.execute(
        db.select(
            date_format_dow(Sale.sale_date).label("dow"),
            func.sum(SaleItem.quantity).label("qty"),
            func.avg(Sale.total_amount).label("avg_sale"),
        )
        .join(SaleItem, SaleItem.sale_id == Sale.id)
        .where(*(
            [SaleItem.product_id == product_id] if product_id else []
        ))
        .group_by(date_format_dow(Sale.sale_date))
        .order_by(date_format_dow(Sale.sale_date))
    ).all()

    dow_names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

    monthly_data = [{"month": r.month, "qty": r.qty, "revenue": float(r.revenue)} for r in monthly]
    # PostgreSQL to_char D gives 1-7 (1=Sunday), SQLite %w gives 0-6 (0=Sunday)
    def _dow_index(val):
        try:
            v = int(val)
            return v - 1 if v >= 1 else v  # normalize to 0-6
        except Exception:
            return 0
    dow_data = [{"day": dow_names[_dow_index(r.dow) % 7], "qty": r.qty, "avg_sale": float(r.avg_sale)} for r in dow]

    # Find peak month and peak day
    peak_month = max(monthly_data, key=lambda x: x["qty"])["month"] if monthly_data else None
    peak_day = max(dow_data, key=lambda x: x["qty"])["day"] if dow_data else None

    return {
        "product_id": product_id,
        "monthly_pattern": monthly_data,
        "day_of_week_pattern": dow_data,
        "peak_month": peak_month,
        "peak_day": peak_day,
        "insight": f"Peak sales in {peak_month}, best day: {peak_day}." if peak_month else "Insufficient data.",
    }


def trend_dashboard() -> dict:
    """Master dashboard endpoint combining all trend insights."""
    return {
        "fast_moving_weekly": fast_moving_products("weekly", 5),
        "fast_moving_monthly": fast_moving_products("monthly", 5),
        "dead_stock": dead_stock_analysis(30),
        "seasonal": seasonal_demand_patterns(),
    }
