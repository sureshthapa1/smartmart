"""Stock Take service — physical inventory count and reconciliation (Feature #3)."""
from __future__ import annotations

from datetime import datetime, timezone

from ..extensions import db
from ..models.product import Product
from ..models.stock_movement import StockMovement
from ..models.stock_take import StockTake, StockTakeItem


def _next_reference() -> str:
    count = db.session.execute(db.select(db.func.count(StockTake.id))).scalar() or 0
    return f"ST-{count + 1:05d}"


def create_stock_take(user_id: int, notes: str = None,
                      product_ids: list[int] | None = None) -> StockTake:
    """Create a new stock take session, snapshotting current system quantities."""
    take = StockTake(
        reference=_next_reference(),
        notes=notes,
        status="in_progress",
        created_by=user_id,
    )
    db.session.add(take)
    db.session.flush()

    # Snapshot products
    if product_ids:
        products = db.session.execute(
            db.select(Product).where(Product.id.in_(product_ids))
        ).scalars().all()
    else:
        products = db.session.execute(db.select(Product).order_by(Product.name)).scalars().all()

    for p in products:
        db.session.add(StockTakeItem(
            stock_take_id=take.id,
            product_id=p.id,
            system_qty=p.quantity,
            counted_qty=None,
        ))

    db.session.commit()
    return take


def update_counts(take_id: int, counts: dict[int, int]) -> StockTake:
    """Update counted quantities. counts = {product_id: counted_qty}"""
    take = db.get_or_404(StockTake, take_id)
    if take.status not in ("draft", "in_progress"):
        raise ValueError("Cannot update a completed or cancelled stock take.")
    for item in take.items:
        if item.product_id in counts:
            item.counted_qty = counts[item.product_id]
    db.session.commit()
    return take


def complete_stock_take(take_id: int, user_id: int, apply_adjustments: bool = True) -> StockTake:
    """Complete the stock take and optionally apply variances to inventory."""
    take = db.get_or_404(StockTake, take_id)
    if take.status == "completed":
        raise ValueError("Stock take is already completed.")

    if apply_adjustments:
        for item in take.items:
            if item.counted_qty is None:
                continue
            variance = item.variance
            if variance == 0:
                continue
            product = db.session.get(Product, item.product_id)
            if not product:
                continue
            product.quantity = item.counted_qty
            change_type = "adjustment_in" if variance > 0 else "adjustment_out"
            db.session.add(StockMovement(
                product_id=product.id,
                change_amount=variance,
                change_type=change_type,
                reference_id=take.id,
                note=f"Stock take #{take.reference} reconciliation",
                created_by=user_id,
                timestamp=datetime.now(timezone.utc),
            ))

    take.status = "completed"
    take.completed_by = user_id
    take.completed_at = datetime.now(timezone.utc)
    db.session.commit()
    return take


def cancel_stock_take(take_id: int) -> StockTake:
    take = db.get_or_404(StockTake, take_id)
    take.status = "cancelled"
    db.session.commit()
    return take


def list_stock_takes(page: int = 1, per_page: int = 20) -> list[StockTake]:
    stmt = (db.select(StockTake)
            .order_by(StockTake.created_at.desc())
            .limit(per_page).offset((page - 1) * per_page))
    return db.session.execute(stmt).scalars().all()
