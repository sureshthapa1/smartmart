"""Stock Transfer between branches."""
from __future__ import annotations
from datetime import datetime, timezone
from ..extensions import db
from ..models.stock_transfer import StockTransfer, StockTransferItem
from ..models.product import Product


def create_transfer(from_branch_id: int, to_branch_id: int, user_id: int,
                    items: list[dict], notes: str | None = None) -> StockTransfer:
    if from_branch_id == to_branch_id:
        raise ValueError("Source and destination branches must be different.")
    if not items:
        raise ValueError("At least one item is required.")

    transfer = StockTransfer(
        from_branch_id=from_branch_id,
        to_branch_id=to_branch_id,
        created_by=user_id,
        notes=notes,
    )
    db.session.add(transfer)
    db.session.flush()

    for item in items:
        product = db.session.get(Product, int(item["product_id"]))
        qty = int(item["quantity"])
        if not product:
            raise ValueError(f"Product {item['product_id']} not found.")
        if product.quantity < qty:
            raise ValueError(f"Insufficient stock for {product.name}. Available: {product.quantity}.")
        db.session.add(StockTransferItem(
            transfer_id=transfer.id,
            product_id=product.id,
            quantity=qty,
        ))

    db.session.commit()
    return transfer


def complete_transfer(transfer_id: int) -> StockTransfer:
    transfer = db.get_or_404(StockTransfer, transfer_id)
    if transfer.status != "pending":
        raise ValueError("Only pending transfers can be completed.")

    for item in transfer.items:
        product = db.session.get(Product, item.product_id)
        if product.quantity < item.quantity:
            raise ValueError(f"Insufficient stock for {product.name}.")
        product.quantity -= item.quantity

    transfer.status = "completed"
    transfer.completed_at = datetime.now(timezone.utc)
    db.session.commit()
    return transfer


def cancel_transfer(transfer_id: int) -> StockTransfer:
    transfer = db.get_or_404(StockTransfer, transfer_id)
    if transfer.status == "completed":
        raise ValueError("Cannot cancel a completed transfer.")
    transfer.status = "cancelled"
    db.session.commit()
    return transfer


def list_transfers() -> list[StockTransfer]:
    return db.session.execute(
        db.select(StockTransfer).order_by(StockTransfer.created_at.desc())
    ).scalars().all()
