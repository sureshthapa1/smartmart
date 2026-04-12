"""Report engine — time-based and analytical reports."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, func

from ..extensions import db
from ..models.product import Product
from ..models.sale import Sale, SaleItem
from ..models.stock_movement import StockMovement
from .db_compat import date_format_year_month, date_format_year_week, date_format_hour


def sales_report(start: date, end: date, granularity: str = "daily") -> list[dict]:
    """Return sales totals grouped by date within [start, end]."""
    rows = db.session.execute(
        db.select(func.date(Sale.sale_date).label("day"), func.sum(Sale.total_amount).label("total"))
        .where(and_(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end))
        .group_by(func.date(Sale.sale_date))
        .order_by(func.date(Sale.sale_date))
    ).all()
    return [{"date": row.day, "total": float(row.total)} for row in rows]


def top_products(start: date, end: date, n: int = 10) -> list[dict]:
    """Return top N products by quantity sold in [start, end], descending."""
    rows = db.session.execute(
        db.select(Product, func.sum(SaleItem.quantity).label("qty_sold"))
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(and_(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end))
        .group_by(Product.id)
        .order_by(func.sum(SaleItem.quantity).desc())
        .limit(n)
    ).all()
    return [{"product": r.Product, "qty_sold": r.qty_sold} for r in rows]


def least_products(start: date, end: date, n: int = 10) -> list[dict]:
    """Return bottom N products by quantity sold in [start, end], ascending."""
    rows = db.session.execute(
        db.select(Product, func.sum(SaleItem.quantity).label("qty_sold"))
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(and_(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end))
        .group_by(Product.id)
        .order_by(func.sum(SaleItem.quantity).asc())
        .limit(n)
    ).all()
    return [{"product": r.Product, "qty_sold": r.qty_sold} for r in rows]


def dead_stock(days: int = 90) -> list[Product]:
    """Return products with zero sales in the past `days` days."""
    cutoff = date.today() - timedelta(days=days)
    sold_ids = db.session.execute(
        db.select(SaleItem.product_id.distinct())
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= cutoff)
    ).scalars().all()
    stmt = db.select(Product)
    if sold_ids:
        stmt = stmt.where(Product.id.notin_(sold_ids))
    return db.session.execute(stmt.order_by(Product.name)).scalars().all()


def profit_per_product(start: date, end: date) -> list[dict]:
    """Return profit per product using historical cost_price stored in SaleItem."""
    rows = db.session.execute(
        db.select(
            Product,
            func.sum(SaleItem.quantity).label("qty_sold"),
            func.sum(SaleItem.subtotal).label("revenue"),
            # Use historical cost if stored, fall back to current product cost
            func.sum(
                func.coalesce(SaleItem.cost_price, Product.cost_price) * SaleItem.quantity
            ).label("cogs"),
        )
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(and_(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end))
        .group_by(Product.id)
    ).all()
    result = []
    for r in rows:
        p = r.Product
        revenue = float(r.revenue)
        cogs = float(r.cogs)
        profit = revenue - cogs
        margin = (profit / revenue * 100) if revenue > 0 else 0
        result.append({
            "product": p,
            "qty_sold": r.qty_sold,
            "revenue": revenue,
            "cost": cogs,
            "profit": profit,
            "margin": round(margin, 2),
        })
    return result


def category_performance(start: date, end: date) -> list[dict]:
    """Return total sales and profit per category in [start, end]."""
    rows = db.session.execute(
        db.select(
            Product.category,
            func.sum(SaleItem.subtotal).label("revenue"),
            func.sum(SaleItem.quantity).label("qty_sold"),
        )
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(and_(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end))
        .group_by(Product.category)
        .order_by(func.sum(SaleItem.subtotal).desc())
    ).all()
    return [{"category": r.category or "Uncategorized", "revenue": float(r.revenue),
             "qty_sold": r.qty_sold} for r in rows]


def inventory_valuation() -> dict:
    """Return per-product valuation (qty * cost_price) and grand total."""
    products = db.session.execute(db.select(Product).order_by(Product.name)).scalars().all()
    items = []
    total = 0.0
    for p in products:
        cost = float(p.cost_price) if p.cost_price else 0.0
        val = round(cost * p.quantity, 2)
        total += val
        items.append({"product": p, "valuation": val})
    return {"items": items, "total": round(total, 2)}


def opening_closing_stock(start: date, end: date) -> list[dict]:
    """Return opening and closing stock per product for [start, end]."""
    products = db.session.execute(db.select(Product).order_by(Product.name)).scalars().all()
    result = []
    for p in products:
        # Opening = current qty minus all movements after start
        after_start = db.session.execute(
            db.select(func.coalesce(func.sum(StockMovement.change_amount), 0))
            .where(and_(StockMovement.product_id == p.id, func.date(StockMovement.timestamp) > end))
        ).scalar() or 0
        closing = p.quantity - after_start
        before_start = db.session.execute(
            db.select(func.coalesce(func.sum(StockMovement.change_amount), 0))
            .where(and_(StockMovement.product_id == p.id,
                        func.date(StockMovement.timestamp) >= start,
                        func.date(StockMovement.timestamp) <= end))
        ).scalar() or 0
        opening = closing - before_start
        result.append({"product": p, "opening": opening, "closing": closing})
    return result


def profitability_analysis(start: date, end: date) -> list[dict]:
    """Return profit, margin, and loss flag per product in [start, end]."""
    return profit_per_product(start, end)


def sales_summary(start: date, end: date) -> dict:
    """Return a high-level sales summary for the period."""
    from ..models.user import User
    total_revenue = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(and_(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end))
    ).scalar() or 0

    total_transactions = db.session.execute(
        db.select(func.count(Sale.id))
        .where(and_(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end))
    ).scalar() or 0

    total_items_sold = db.session.execute(
        db.select(func.coalesce(func.sum(SaleItem.quantity), 0))
        .join(Sale, SaleItem.sale_id == Sale.id)
        .where(and_(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end))
    ).scalar() or 0

    avg_transaction = float(total_revenue) / total_transactions if total_transactions else 0

    # Best single day
    best_day_row = db.session.execute(
        db.select(func.date(Sale.sale_date).label("day"), func.sum(Sale.total_amount).label("total"))
        .where(and_(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end))
        .group_by(func.date(Sale.sale_date))
        .order_by(func.sum(Sale.total_amount).desc())
        .limit(1)
    ).first()

    return {
        "total_revenue": float(total_revenue),
        "total_transactions": total_transactions,
        "total_items_sold": int(total_items_sold),
        "avg_transaction": round(avg_transaction, 2),
        "best_day": str(best_day_row.day) if best_day_row else "—",
        "best_day_revenue": float(best_day_row.total) if best_day_row else 0,
    }


def sales_by_period(start: date, end: date, period: str = "daily") -> list[dict]:
    """Return sales grouped by day/week/month."""
    if period == "weekly":
        rows = db.session.execute(
            db.select(
                date_format_year_week(Sale.sale_date).label("period"),
                func.sum(Sale.total_amount).label("total"),
                func.count(Sale.id).label("transactions"),
            )
            .where(and_(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end))
            .group_by(date_format_year_week(Sale.sale_date))
            .order_by(date_format_year_week(Sale.sale_date))
        ).all()
    elif period == "monthly":
        rows = db.session.execute(
            db.select(
                date_format_year_month(Sale.sale_date).label("period"),
                func.sum(Sale.total_amount).label("total"),
                func.count(Sale.id).label("transactions"),
            )
            .where(and_(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end))
            .group_by(date_format_year_month(Sale.sale_date))
            .order_by(date_format_year_month(Sale.sale_date))
        ).all()
    else:  # daily
        rows = db.session.execute(
            db.select(
                func.date(Sale.sale_date).label("period"),
                func.sum(Sale.total_amount).label("total"),
                func.count(Sale.id).label("transactions"),
            )
            .where(and_(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end))
            .group_by(func.date(Sale.sale_date))
            .order_by(func.date(Sale.sale_date))
        ).all()
    return [{"period": str(r.period), "total": float(r.total), "transactions": r.transactions} for r in rows]


def product_wise_sales(start: date, end: date) -> list[dict]:
    """Return detailed sales breakdown per product using historical cost."""
    rows = db.session.execute(
        db.select(
            Product,
            func.sum(SaleItem.quantity).label("qty_sold"),
            func.sum(SaleItem.subtotal).label("revenue"),
            func.sum(
                func.coalesce(SaleItem.cost_price, Product.cost_price) * SaleItem.quantity
            ).label("cogs"),
            func.count(SaleItem.id.distinct()).label("times_sold"),
        )
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(and_(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end))
        .group_by(Product.id)
        .order_by(func.sum(SaleItem.subtotal).desc())
    ).all()
    result = []
    for r in rows:
        p = r.Product
        revenue = float(r.revenue)
        cogs = float(r.cogs)
        profit = revenue - cogs
        result.append({
            "product": p,
            "qty_sold": r.qty_sold,
            "revenue": revenue,
            "cost": cogs,
            "profit": profit,
            "times_sold": r.times_sold,
        })
    return result


def staff_sales_report(start: date, end: date) -> list[dict]:
    """Return revenue and transaction count per staff member."""
    from ..models.user import User
    rows = db.session.execute(
        db.select(
            User.username,
            User.role,
            func.count(Sale.id).label("transactions"),
            func.sum(Sale.total_amount).label("revenue"),
            func.sum(SaleItem.quantity).label("items_sold"),
        )
        .join(Sale, Sale.user_id == User.id)
        .join(SaleItem, SaleItem.sale_id == Sale.id)
        .where(and_(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end))
        .group_by(User.id)
        .order_by(func.sum(Sale.total_amount).desc())
    ).all()
    return [{
        "username": r.username,
        "role": r.role,
        "transactions": r.transactions,
        "revenue": float(r.revenue),
        "items_sold": r.items_sold,
        "avg_sale": round(float(r.revenue) / r.transactions, 2) if r.transactions else 0,
    } for r in rows]


def hourly_sales(start: date, end: date) -> list[dict]:
    """Return sales grouped by hour of day (peak hours analysis)."""
    rows = db.session.execute(
        db.select(
            date_format_hour(Sale.sale_date).label("hour"),
            func.sum(Sale.total_amount).label("total"),
            func.count(Sale.id).label("transactions"),
        )
        .where(and_(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end))
        .group_by(date_format_hour(Sale.sale_date))
        .order_by(date_format_hour(Sale.sale_date))
    ).all()
    return [{"hour": int(r.hour), "label": f"{int(r.hour):02d}:00",
             "total": float(r.total), "transactions": r.transactions} for r in rows]


def staff_efficiency_report(start: date, end: date) -> list[dict]:
    """Comprehensive per-staff efficiency metrics."""
    from ..models.user import User
    from ..models.sale import Sale, SaleItem

    users = db.session.execute(db.select(User).order_by(User.username)).scalars().all()
    result = []

    for user in users:
        # Basic sales metrics
        sales = db.session.execute(
            db.select(Sale)
            .where(and_(
                Sale.user_id == user.id,
                func.date(Sale.sale_date) >= start,
                func.date(Sale.sale_date) <= end,
            ))
        ).scalars().all()

        if not sales:
            continue

        sale_ids = [s.id for s in sales]
        total_revenue = sum(float(s.total_amount) for s in sales)
        total_discount = sum(float(s.discount_amount or 0) for s in sales)
        total_transactions = len(sales)

        # Items sold
        items_sold = db.session.execute(
            db.select(func.coalesce(func.sum(SaleItem.quantity), 0))
            .where(SaleItem.sale_id.in_(sale_ids))
        ).scalar() or 0

        # Avg sale value
        avg_sale = total_revenue / total_transactions if total_transactions else 0

        # Peak hour
        peak_row = db.session.execute(
            db.select(
                date_format_hour(Sale.sale_date).label("hour"),
                func.count(Sale.id).label("cnt")
            )
            .where(Sale.id.in_(sale_ids))
            .group_by(date_format_hour(Sale.sale_date))
            .order_by(func.count(Sale.id).desc())
            .limit(1)
        ).first()
        peak_hour = f"{int(peak_row.hour):02d}:00" if peak_row else "—"

        # Top product sold by this staff
        top_prod_row = db.session.execute(
            db.select(Product, func.sum(SaleItem.quantity).label("qty"))
            .join(SaleItem, SaleItem.product_id == Product.id)
            .where(SaleItem.sale_id.in_(sale_ids))
            .group_by(Product.id)
            .order_by(func.sum(SaleItem.quantity).desc())
            .limit(1)
        ).first()
        top_product = top_prod_row.Product.name if top_prod_row else "—"
        top_product_qty = top_prod_row.qty if top_prod_row else 0

        # Active days
        active_days = db.session.execute(
            db.select(func.count(func.date(Sale.sale_date).distinct()))
            .where(Sale.id.in_(sale_ids))
        ).scalar() or 0

        # Sales per active day
        sales_per_day = round(total_transactions / active_days, 1) if active_days else 0

        # Discount rate
        gross = sum(
            float(si.unit_price) * si.quantity
            for s in sales for si in s.items
        )
        discount_rate = round((total_discount / gross * 100), 1) if gross else 0

        result.append({
            "user": user,
            "total_transactions": total_transactions,
            "total_revenue": total_revenue,
            "total_discount": total_discount,
            "discount_rate": discount_rate,
            "items_sold": int(items_sold),
            "avg_sale": round(avg_sale, 2),
            "peak_hour": peak_hour,
            "top_product": top_product,
            "top_product_qty": top_product_qty,
            "active_days": active_days,
            "sales_per_day": sales_per_day,
        })

    # Sort by revenue descending
    result.sort(key=lambda x: x["total_revenue"], reverse=True)
    return result
