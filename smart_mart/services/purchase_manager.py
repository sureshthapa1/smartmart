"""Purchase management service — create purchases, manage suppliers, and update stock."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import and_

from ..extensions import db
from ..models.product import Product
from ..models.purchase import Purchase, PurchaseItem
from ..models.stock_movement import StockMovement
from ..models.supplier import Supplier


def create_purchase(supplier_id: int, items: list[dict], purchase_date: date, user_id: int) -> Purchase:
    """Create a confirmed purchase, increase product stock, and record movements."""
    try:
        total_cost = sum(item["unit_cost"] * item["quantity"] for item in items)
        purchase = Purchase(
            supplier_id=supplier_id,
            purchase_date=purchase_date,
            total_cost=total_cost,
            created_by=user_id,
        )
        db.session.add(purchase)
        db.session.flush()

        for item in items:
            product = db.session.get(Product, item["product_id"])
            if product is None:
                raise ValueError(f"Product with id {item['product_id']} not found.")
            qty = item["quantity"]
            unit_cost = item["unit_cost"]
            db.session.add(PurchaseItem(
                purchase_id=purchase.id, product_id=product.id,
                quantity=qty, unit_cost=unit_cost, subtotal=unit_cost * qty,
            ))
            product.quantity += qty
            db.session.add(StockMovement(
                product_id=product.id, change_amount=qty, change_type="purchase",
                reference_id=purchase.id, created_by=user_id, timestamp=datetime.now(timezone.utc),
            ))

        try:
            from . import cash_flow_manager
            cash_flow_manager.record_expense(
                expense_type="purchase", amount=purchase.total_cost,
                expense_date=purchase_date, user_id=user_id,
            )
        except (ImportError, AttributeError):
            pass

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return purchase


def list_purchases(filters: dict, page: int = 1, per_page: int = 20) -> list[Purchase]:
    """Return purchases ordered by date descending, optionally filtered by date range."""
    stmt = db.select(Purchase).order_by(Purchase.purchase_date.desc())
    conditions = []
    if filters.get("start_date"):
        conditions.append(Purchase.purchase_date >= filters["start_date"])
    if filters.get("end_date"):
        conditions.append(Purchase.purchase_date <= filters["end_date"])
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = stmt.limit(per_page).offset((page - 1) * per_page)
    return db.session.execute(stmt).scalars().all()


def create_supplier(data: dict) -> Supplier:
    """Create and persist a new supplier."""
    supplier = Supplier(
        name=data["name"],
        contact=data.get("contact"),
        email=data.get("email"),
        address=data.get("address"),
    )
    db.session.add(supplier)
    db.session.commit()
    return supplier


def update_supplier(supplier_id: int, data: dict) -> Supplier:
    """Update an existing supplier."""
    supplier = db.get_or_404(Supplier, supplier_id)
    for field in ("name", "contact", "email", "address"):
        if field in data:
            setattr(supplier, field, data[field])
    db.session.commit()
    return supplier


def delete_supplier(supplier_id: int) -> None:
    """Delete a supplier. Raises ValueError if it has purchases."""
    supplier = db.get_or_404(Supplier, supplier_id)
    if supplier.purchases.count() > 0:
        raise ValueError(f"Cannot delete '{supplier.name}': has associated purchase records.")
    db.session.delete(supplier)
    db.session.commit()


def list_suppliers() -> list[Supplier]:
    """Return all suppliers ordered by name."""
    return db.session.execute(db.select(Supplier).order_by(Supplier.name)).scalars().all()
