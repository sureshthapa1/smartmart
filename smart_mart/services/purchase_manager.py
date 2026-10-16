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
            # Update cost price only when unset (first purchase) — never overwrite
            # an established cost price with a one-off delivery price, as that would
            # corrupt historical profitability calculations for all prior sales.
            if not product.cost_price or product.cost_price == 0:
                product.cost_price = unit_cost
            # Auto-link supplier to product if not already set
            if not product.supplier_id:
                product.supplier_id = supplier_id
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

        try:
            from . import operations_manager
            operations_manager.create_batches_for_purchase(purchase)
        except Exception:
            raise  # outer except handles rollback

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
    has_purchases = db.session.execute(
        db.select(Purchase.id).where(Purchase.supplier_id == supplier_id).limit(1)
    ).first()
    if has_purchases:
        raise ValueError(f"Cannot delete '{supplier.name}': has associated purchase records.")
    db.session.delete(supplier)
    db.session.commit()


def list_suppliers() -> list[Supplier]:
    """Return all suppliers ordered by name."""
    return db.session.execute(db.select(Supplier).order_by(Supplier.name)).scalars().all()


def delete_purchase(purchase_id: int, user_id: int) -> None:
    """Delete a purchase record and reverse its stock movements.

    Refuses rather than half-reversing. A purchase is only safely erasable while
    the goods it brought in are all still on the shelf and nothing else has been
    booked against it, so this raises ValueError if:

    * a supplier return references it (pre-existing guard — audit integrity);
    * a supplier payment references it (money was paid against this purchase);
    * a batch it created has been drawn down (units already went out);
    * reversing a line would drive stock negative.

    The last check replaced ``max(0, quantity - item.quantity)``. That clamp made
    the failure invisible: delete a 100-unit purchase after 60 units were sold and
    stock went 40 → 0, destroying the 40 units still physically in the shop, while
    the StockMovement row logged the full -100 the clamp had not actually applied.
    """
    from ..models.operations import ProductBatch, SupplierPayment
    from ..models.supplier_return import SupplierReturn
    purchase = db.get_or_404(Purchase, purchase_id)

    # Guard: block deletion if supplier returns reference this purchase
    has_returns = db.session.execute(
        db.select(db.func.count(SupplierReturn.id))
        .where(SupplierReturn.purchase_id == purchase_id)
    ).scalar() or 0
    if has_returns:
        raise ValueError(
            f"Cannot delete Purchase #{purchase_id}: "
            "it has associated supplier return records. Void the returns first."
        )

    # Guard: supplier payments carry no cascade, so deleting the purchase would
    # leave them pointing at a row that no longer exists.
    has_payments = db.session.execute(
        db.select(db.func.count(SupplierPayment.id))
        .where(SupplierPayment.purchase_id == purchase_id)
    ).scalar() or 0
    if has_payments:
        raise ValueError(
            f"Cannot delete Purchase #{purchase_id}: "
            "supplier payments are recorded against it. Void the payments first."
        )

    # Batches created by this purchase. There is no Purchase→ProductBatch
    # relationship, so nothing cascaded: the batch rows survived the delete with a
    # dangling purchase_id and their original quantity_remaining, and FIFO/expiry
    # logic kept counting stock the purchase no longer accounted for.
    batches = db.session.execute(
        db.select(ProductBatch).where(ProductBatch.purchase_id == purchase_id)
    ).scalars().all()
    drawn_down = [b for b in batches if b.quantity_remaining != b.quantity_received]
    if drawn_down:
        raise ValueError(
            f"Cannot delete Purchase #{purchase_id}: stock from batch "
            f"{drawn_down[0].batch_code} has already been used. "
            "Record a stock adjustment instead of deleting the purchase."
        )

    # Reverse stock for each item — all-or-nothing, checked before any mutation.
    reversals = []
    for item in purchase.items:
        product = db.session.get(Product, item.product_id)
        if product is None:
            continue
        if product.quantity < item.quantity:
            raise ValueError(
                f"Cannot delete Purchase #{purchase_id}: reversing "
                f"{item.quantity} of '{product.name}' would leave negative stock "
                f"(only {product.quantity} on hand). Those units have already "
                "moved out; record a stock adjustment instead."
            )
        reversals.append((product, item.quantity))

    for product, qty in reversals:
        product.quantity -= qty
        db.session.add(StockMovement(
            product_id=product.id,
            change_amount=-qty,
            change_type="purchase_reversal",
            reference_id=purchase.id,
            created_by=user_id,
            timestamp=datetime.now(timezone.utc),
        ))

    for batch in batches:
        db.session.delete(batch)

    try:
        from . import audit_service
        audit_service.log("delete", "Purchase", purchase.id,
                          f"Purchase #{purchase.id} (supplier {purchase.supplier_id})",
                          changes={"deleted_by": [None, str(user_id)]})
    except Exception:
        pass

    db.session.delete(purchase)
    db.session.commit()
