"""Supplier Return service — return goods to supplier (Feature #5)."""
from __future__ import annotations

from datetime import datetime, timezone

from ..extensions import db
from ..models.product import Product
from ..models.stock_movement import StockMovement
from ..models.supplier_return import SupplierReturn, SupplierReturnItem


def _next_reference() -> str:
    count = db.session.execute(db.select(db.func.count(SupplierReturn.id))).scalar() or 0
    return f"SR-{count + 1:05d}"


def create_supplier_return(supplier_id: int, items: list[dict], user_id: int,
                           purchase_id: int | None = None,
                           reason: str | None = None,
                           notes: str | None = None) -> SupplierReturn:
    """Create a supplier return and deduct stock immediately."""
    sr = SupplierReturn(
        reference=_next_reference(),
        supplier_id=supplier_id,
        purchase_id=purchase_id,
        reason=reason,
        notes=notes,
        status="pending",
        created_by=user_id,
    )
    db.session.add(sr)
    db.session.flush()

    total_credit = 0.0
    for item in items:
        product = db.session.get(Product, item["product_id"])
        if not product:
            raise ValueError(f"Product #{item['product_id']} not found.")
        qty = int(item["quantity"])
        unit_cost = float(item["unit_cost"])
        if qty > product.quantity:
            raise ValueError(
                f"Insufficient stock for '{product.name}': "
                f"requested {qty}, available {product.quantity}."
            )
        db.session.add(SupplierReturnItem(
            supplier_return_id=sr.id,
            product_id=product.id,
            quantity=qty,
            unit_cost=unit_cost,
            reason=item.get("reason"),
        ))
        # Deduct stock
        product.quantity -= qty
        db.session.add(StockMovement(
            product_id=product.id,
            change_amount=-qty,
            change_type="supplier_return",
            reference_id=sr.id,
            note=f"Supplier return {sr.reference}",
            created_by=user_id,
            timestamp=datetime.now(timezone.utc),
        ))
        total_credit += qty * unit_cost

    sr.credit_amount = total_credit
    db.session.commit()
    return sr


def update_status(return_id: int, status: str) -> SupplierReturn:
    sr = db.get_or_404(SupplierReturn, return_id)
    valid = ("pending", "sent", "credited", "cancelled")
    if status not in valid:
        raise ValueError(f"Invalid status. Must be one of: {', '.join(valid)}")
    if status == "cancelled" and sr.status != "pending":
        raise ValueError("Only pending returns can be cancelled.")
    if status == "sent":
        sr.sent_at = datetime.now(timezone.utc)
    # If cancelling, restore stock
    if status == "cancelled":
        for item in sr.items:
            product = db.session.get(Product, item.product_id)
            if product:
                product.quantity += item.quantity
                db.session.add(StockMovement(
                    product_id=product.id,
                    change_amount=item.quantity,
                    change_type="adjustment_in",
                    reference_id=sr.id,
                    note=f"Supplier return {sr.reference} cancelled",
                    created_by=sr.created_by,
                    timestamp=datetime.now(timezone.utc),
                ))
    sr.status = status
    db.session.commit()
    return sr


def list_supplier_returns(page: int = 1, per_page: int = 20) -> list[SupplierReturn]:
    stmt = (db.select(SupplierReturn)
            .order_by(SupplierReturn.created_at.desc())
            .limit(per_page).offset((page - 1) * per_page))
    return db.session.execute(stmt).scalars().all()
