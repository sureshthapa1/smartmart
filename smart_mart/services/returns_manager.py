from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from ..extensions import db
from ..models.product import Product
from ..models.sale import Sale, SaleItem
from ..models.sale_return import SaleReturn, SaleReturnItem
from ..models.stock_movement import StockMovement


def _to_money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def get_sale(sale_id: int) -> Sale:
    return db.get_or_404(Sale, sale_id)


def list_returns(limit: int | None = 50) -> list[SaleReturn]:
    stmt = db.select(SaleReturn).order_by(SaleReturn.created_at.desc())
    if limit:
        stmt = stmt.limit(limit)
    return db.session.execute(stmt).scalars().all()


def get_return(return_id: int) -> SaleReturn:
    return db.get_or_404(SaleReturn, return_id)


def returned_quantity_for_sale_item(sale_item_id: int) -> int:
    qty = db.session.execute(
        db.select(db.func.coalesce(db.func.sum(SaleReturnItem.quantity), 0)).where(
            SaleReturnItem.sale_item_id == sale_item_id
        )
    ).scalar()
    return int(qty or 0)


def returnable_items_for_sale(sale_id: int) -> list[dict]:
    sale = get_sale(sale_id)
    rows: list[dict] = []
    for item in sale.items:
        already_returned = returned_quantity_for_sale_item(item.id)
        remaining = max(0, item.quantity - already_returned)
        rows.append(
            {
                "sale_item": item,
                "already_returned": already_returned,
                "remaining_quantity": remaining,
            }
        )
    return rows


def create_return(
    sale_id: int,
    user_id: int,
    item_quantities: list[dict],
    refund_mode: str = "cash",
    reason: str | None = None,
    notes: str | None = None,
) -> SaleReturn:
    sale = get_sale(sale_id)
    if not item_quantities:
        raise ValueError("Select at least one item quantity to return.")

    requested_by_item_id = {}
    for row in item_quantities:
        sale_item_id = int(row["sale_item_id"])
        quantity = int(row["quantity"])
        if quantity <= 0:
            continue
        requested_by_item_id[sale_item_id] = quantity

    if not requested_by_item_id:
        raise ValueError("Select at least one item quantity to return.")

    refund_amount = Decimal("0.00")
    sale_return = SaleReturn(
        sale_id=sale.id,
        processed_by=user_id,
        refund_mode=(refund_mode or "cash").strip().lower(),
        reason=(reason or "").strip() or None,
        notes=(notes or "").strip() or None,
        refund_amount=Decimal("0.00"),
    )

    try:
        db.session.add(sale_return)
        db.session.flush()

        for item in sale.items:
            requested_qty = requested_by_item_id.get(item.id, 0)
            if requested_qty <= 0:
                continue

            already_returned = returned_quantity_for_sale_item(item.id)
            remaining = item.quantity - already_returned
            if requested_qty > remaining:
                raise ValueError(
                    f"Cannot return {requested_qty} of '{item.product.name}'; "
                    f"only {remaining} remaining from the original sale."
                )

            product = db.session.get(Product, item.product_id)
            if product is None:
                raise ValueError(f"Product for sale item #{item.id} no longer exists.")

            subtotal = _to_money(item.unit_price) * requested_qty
            refund_amount += subtotal

            db.session.add(
                SaleReturnItem(
                    sale_return_id=sale_return.id,
                    sale_item_id=item.id,
                    product_id=item.product_id,
                    quantity=requested_qty,
                    unit_price=item.unit_price,
                    subtotal=subtotal,
                )
            )

            product.quantity += requested_qty
            db.session.add(
                StockMovement(
                    product_id=product.id,
                    change_amount=requested_qty,
                    change_type="sale_return",
                    reference_id=sale_return.id,
                    note=f"Return for sale #{sale.id}",
                    created_by=user_id,
                    timestamp=datetime.now(timezone.utc),
                )
            )

        if refund_amount <= 0:
            raise ValueError("Select at least one item quantity to return.")

        sale_return.refund_amount = refund_amount
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return sale_return
