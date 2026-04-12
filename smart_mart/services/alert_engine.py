"""Alert engine — low stock, expiry, and high-demand alerts."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func

from ..extensions import db
from ..models.product import Product
from ..models.sale import SaleItem, Sale


def get_low_stock_alerts(threshold: int | None = None) -> list[Product]:
    """Return products at or below their individual reorder_point (or global threshold)."""
    if threshold is None:
        try:
            from flask import current_app
            threshold = current_app.config.get("LOW_STOCK_THRESHOLD", 10)
        except Exception:
            threshold = 10
    # Use per-product reorder_point if set, else global threshold
    from sqlalchemy import case
    effective_threshold = case(
        (Product.reorder_point.isnot(None), Product.reorder_point),
        else_=threshold
    )
    stmt = (db.select(Product)
            .where(Product.quantity <= effective_threshold)
            .order_by(Product.quantity))
    return db.session.execute(stmt).scalars().all()


def get_expiry_alerts(days: int | None = None) -> list[Product]:
    """Return products expiring within the warning window (default 30 days)."""
    if days is None:
        from flask import current_app
        days = current_app.config.get("EXPIRY_WARNING_DAYS", 30)
    today = date.today()
    cutoff = today + timedelta(days=days)
    stmt = (
        db.select(Product)
        .where(Product.expiry_date.isnot(None))
        .where(Product.expiry_date >= today)
        .where(Product.expiry_date <= cutoff)
        .order_by(Product.expiry_date)
    )
    return db.session.execute(stmt).scalars().all()


def get_high_demand_alerts(threshold: int | None = None) -> list[dict]:
    """Return products whose total quantity sold in the past 7 days exceeds threshold (default 50)."""
    if threshold is None:
        from flask import current_app
        threshold = current_app.config.get("HIGH_DEMAND_THRESHOLD", 50)
    cutoff = date.today() - timedelta(days=7)
    rows = db.session.execute(
        db.select(Product, func.sum(SaleItem.quantity).label("total_sold"))
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= cutoff)
        .group_by(Product.id)
        .having(func.sum(SaleItem.quantity) > threshold)
        .order_by(func.sum(SaleItem.quantity).desc())
    ).all()
    return [{"product": row.Product, "total_sold": row.total_sold} for row in rows]


def get_all_alerts() -> dict:
    """Return all active alerts grouped by type."""
    return {
        "low_stock": get_low_stock_alerts(),
        "expiry": get_expiry_alerts(),
        "high_demand": get_high_demand_alerts(),
    }
