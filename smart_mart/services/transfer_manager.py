"""Stock Transfer between branches."""
from __future__ import annotations
from datetime import datetime, timezone
from ..extensions import db
from ..models.stock_movement import StockMovement
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


def complete_transfer(transfer_id: int, user_id: int | None = None) -> StockTransfer:
    """Deduct the transferred units from the source branch's stock.

    Writes a ``transfer_out`` StockMovement per line. Without it the units left
    the shop with no ledger entry at all, so ``products.quantity`` and the stock
    ledger drifted apart permanently and the shrinkage report counted the
    transfer as unexplained loss. The BI ledger already had a "Transfer Out"
    filter waiting for rows nothing ever wrote.

    Nothing is credited anywhere, because ``products`` has no ``branch_id`` — the
    schema has no per-branch stock to credit. So a completed transfer is, in the
    data, a write-off from the source. That is the existing behaviour and this
    change only makes it auditable; making transfers actually move stock between
    branches needs per-branch inventory.
    """
    transfer = db.get_or_404(StockTransfer, transfer_id)
    if transfer.status != "pending":
        raise ValueError("Only pending transfers can be completed.")

    # created_by is NOT NULL on stock_movements; fall back to whoever raised the
    # transfer when the caller does not pass the acting user.
    actor_id = user_id or transfer.created_by
    now = datetime.now(timezone.utc)

    for item in transfer.items:
        product = db.session.execute(
            db.select(Product).where(Product.id == item.product_id).with_for_update()
        ).scalar_one_or_none()
        if product is None:
            raise ValueError(f"Product {item.product_id} no longer exists.")
        if product.quantity < item.quantity:
            raise ValueError(f"Insufficient stock for {product.name}.")
        from ..utils.qty import qty_sub as _qty_sub
        _qty_sub(product, item.quantity)
        db.session.add(StockMovement(
            product_id=product.id,
            change_amount=-item.quantity,
            change_type="transfer_out",
            reference_id=transfer.id,
            note=(f"Transfer #{transfer.id}: branch {transfer.from_branch_id} "
                  f"→ branch {transfer.to_branch_id}"),
            created_by=actor_id,
            timestamp=now,
        ))

    transfer.status = "completed"
    transfer.completed_at = now
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
