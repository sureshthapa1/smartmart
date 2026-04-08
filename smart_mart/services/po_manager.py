"""Purchase Order management service."""
from __future__ import annotations
from datetime import datetime, timezone, date
from ..extensions import db
from ..models.purchase_order import PurchaseOrder, PurchaseOrderItem
from ..models.product import Product


def _next_po_number() -> str:
    count = db.session.execute(db.select(db.func.count(PurchaseOrder.id))).scalar() or 0
    return f"PO-{datetime.now().strftime('%Y%m')}-{count + 1:04d}"


def create_po(supplier_id: int, items: list[dict], user_id: int,
              expected_date: date | None = None, notes: str | None = None) -> PurchaseOrder:
    if not items:
        raise ValueError("At least one item is required.")
    po = PurchaseOrder(
        po_number=_next_po_number(),
        supplier_id=supplier_id,
        created_by=user_id,
        expected_date=expected_date,
        notes=notes,
    )
    db.session.add(po)
    db.session.flush()
    for item in items:
        db.session.add(PurchaseOrderItem(
            order_id=po.id,
            product_id=int(item["product_id"]),
            quantity_ordered=int(item["quantity"]),
            unit_cost=float(item["unit_cost"]),
        ))
    db.session.commit()
    return po


def send_po(po_id: int) -> PurchaseOrder:
    po = db.get_or_404(PurchaseOrder, po_id)
    if po.status != "draft":
        raise ValueError("Only draft POs can be sent.")
    po.status = "sent"
    po.sent_at = datetime.now(timezone.utc)
    db.session.commit()
    return po


def receive_po(po_id: int, user_id: int, received_quantities: dict[int, int]) -> PurchaseOrder:
    """Mark items as received and auto-create a Purchase record."""
    from ..services.purchase_manager import create_purchase
    po = db.get_or_404(PurchaseOrder, po_id)
    if po.status not in ("sent", "partial"):
        raise ValueError("PO must be sent before receiving.")

    items_for_purchase = []
    all_received = True
    for item in po.items:
        qty = received_quantities.get(item.id, 0)
        item.quantity_received = min(item.quantity_ordered, item.quantity_received + qty)
        if item.quantity_received < item.quantity_ordered:
            all_received = False
        if qty > 0:
            items_for_purchase.append({
                "product_id": item.product_id,
                "quantity": qty,
                "unit_cost": float(item.unit_cost),
            })

    po.status = "received" if all_received else "partial"
    if all_received:
        po.received_at = datetime.now(timezone.utc)

    if items_for_purchase:
        purchase = create_purchase(
            supplier_id=po.supplier_id,
            items=items_for_purchase,
            purchase_date=date.today(),
            user_id=user_id,
        )
        po.purchase_id = purchase.id

    db.session.commit()
    return po


def cancel_po(po_id: int) -> PurchaseOrder:
    po = db.get_or_404(PurchaseOrder, po_id)
    if po.status == "received":
        raise ValueError("Cannot cancel a fully received PO.")
    po.status = "cancelled"
    db.session.commit()
    return po


def list_pos(status: str | None = None) -> list[PurchaseOrder]:
    stmt = db.select(PurchaseOrder).order_by(PurchaseOrder.created_at.desc())
    if status:
        stmt = stmt.where(PurchaseOrder.status == status)
    return db.session.execute(stmt).scalars().all()
