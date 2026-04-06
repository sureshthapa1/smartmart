"""Report engine — time-based and analytical reports."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, func

from ..extensions import db
from ..models.product import Product
from ..models.sale import Sale, SaleItem
from ..models.stock_movement import StockMovement


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
    """Return profit per product = (unit_price - cost_price) * qty_sold in [start, end]."""
    rows = db.session.execute(
        db.select(
            Product,
            func.sum(SaleItem.quantity).label("qty_sold"),
            func.sum(SaleItem.subtotal).label("revenue"),
            # cost per unit at time of sale = product's current cost_price
        )
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(and_(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end))
        .group_by(Product.id)
    ).all()
    result = []
    for r in rows:
        p = r.Product
        cost = Decimal(str(p.cost_price))
        sell = Decimal(str(p.selling_price))
        qty = r.qty_sold
        # profit = (selling_price - cost_price) * qty_sold
        profit = (sell - cost) * qty
        margin = ((sell - cost) / sell * 100) if sell > 0 else Decimal("0")
        result.append({
            "product": p,
            "qty_sold": qty,
            "revenue": float(r.revenue),
            "cost": float(cost * qty),
            "profit": float(profit),
            "margin": float(margin),
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
