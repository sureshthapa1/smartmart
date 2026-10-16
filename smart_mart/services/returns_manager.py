from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from ..extensions import db
from ..models.product import Product
from ..models.product_variant import ProductVariant
from ..models.sale import Sale, SaleItem
from ..models.sale_return import SaleReturn, SaleReturnItem
from ..models.stock_movement import StockMovement

logger = logging.getLogger(__name__)


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
                item_label = (
                    item.custom_label
                    or (item.product.name if item.product else f"item #{item.id}")
                )
                raise ValueError(
                    f"Cannot return {requested_qty} of '{item_label}'; "
                    f"only {remaining} remaining from the original sale."
                )

            product = db.session.get(Product, item.product_id)
            if product is None:
                if item.product_id is None:
                    # Custom/loose item — no inventory to restock, still allow refund
                    subtotal = _to_money(item.unit_price) * requested_qty
                    refund_amount += subtotal
                    db.session.add(
                        SaleReturnItem(
                            sale_return_id=sale_return.id,
                            sale_item_id=item.id,
                            product_id=None,
                            quantity=requested_qty,
                            unit_price=item.unit_price,
                            subtotal=subtotal,
                        )
                    )
                    continue
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

            # ── Restock the bucket the units came out of ───────────────────
            # create_sale deducts variant stock for a variant line, so crediting
            # the parent product here invented parent units the shop never had
            # while the returned variant units stayed missing — one return drifted
            # inventory in both directions at once. stock_movements has no
            # variant_id column, so the ledger row stays keyed to the parent
            # product and names the variant in the note.
            restock_note = f"Return for sale #{sale.id}"
            if item.variant_id:
                variant = db.session.get(ProductVariant, item.variant_id)
                if variant is None:
                    # variant_id is ON DELETE SET NULL, so a live id pointing at a
                    # missing row should not be reachable. Skip the restock rather
                    # than credit the parent and corrupt its count — but still
                    # refund, because blocking a customer at the counter over an
                    # internal inconsistency is the worse failure. The refund row
                    # is already recorded above; no ledger entry, since no stock
                    # actually moved.
                    logger.warning(
                        "Sale #%s item %s references missing variant %s — "
                        "refunded but stock not restocked.",
                        sale.id, item.id, item.variant_id,
                    )
                    continue
                variant.quantity += requested_qty
                restock_note = (f"Return for sale #{sale.id} — variant: "
                                f"{variant.variant_name} ({variant.sku})")
            else:
                product.quantity += requested_qty

            db.session.add(
                StockMovement(
                    product_id=product.id,
                    change_amount=requested_qty,
                    change_type="sale_return",
                    reference_id=sale_return.id,
                    note=restock_note,
                    created_by=user_id,
                    timestamp=datetime.now(timezone.utc),
                )
            )

        if refund_amount <= 0:
            raise ValueError("Select at least one item quantity to return.")

        sale_return.refund_amount = refund_amount

        # ── Reduce customer total_spent by the refund amount ──────────────
        # Without this, voided/returned items permanently inflate the customer's
        # spending total, causing incorrect loyalty tier upgrades.
        if sale.customer_id:
            from ..models.customer import Customer
            cust = db.session.get(Customer, sale.customer_id)
            if cust:
                cust.total_spent = max(0.0, float(cust.total_spent or 0) - float(refund_amount))
                ts = cust.total_spent
                if ts >= 100000:
                    cust.loyalty_tier = "platinum"
                elif ts >= 50000:
                    cust.loyalty_tier = "gold"
                elif ts >= 20000:
                    cust.loyalty_tier = "silver"
                else:
                    cust.loyalty_tier = "bronze"

        # ── Credit sale: reduce the outstanding balance by the refund amount ──
        # Without this, a customer who returns goods on a credit sale still owes
        # the full original amount on the Udharo ledger.
        if sale.payment_mode == "credit" and not sale.credit_collected:
            from ..models.operations import CustomerCreditPayment
            # Compute current outstanding balance so we don't over-credit
            already_paid = db.session.execute(
                db.select(db.func.coalesce(db.func.sum(CustomerCreditPayment.amount), 0))
                .where(CustomerCreditPayment.sale_id == sale.id)
            ).scalar() or Decimal("0.00")
            outstanding = max(Decimal("0.00"), Decimal(str(sale.total_amount)) - Decimal(str(already_paid)))
            credit_reduction = min(refund_amount, outstanding)
            if credit_reduction > 0:
                db.session.add(CustomerCreditPayment(
                    sale_id=sale.id,
                    user_id=user_id,
                    amount=credit_reduction,
                    payment_mode="return",
                    note=f"Return #{sale_return.id} — goods returned",
                ))
                new_balance = outstanding - credit_reduction
                sale.credit_collected = new_balance <= Decimal("0.00")

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return sale_return
