from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func

from ..extensions import db
from ..models.customer import Customer
from ..models.expense import Expense
from ..models.operations import (
    AppNotification,
    Branch,
    CashSession,
    CustomerCreditPayment,
    CustomerLoyaltyTransaction,
    ProductBatch,
    ProductInventoryProfile,
    SupplierPayment,
)
from ..models.product import Product
from ..models.purchase import Purchase
from ..models.sale import Sale, SaleItem
from ..models.supplier import Supplier


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _profile_for(product_id: int) -> ProductInventoryProfile:
    profile = db.session.execute(
        db.select(ProductInventoryProfile).where(ProductInventoryProfile.product_id == product_id)
    ).scalar_one_or_none()
    if profile is None:
        profile = ProductInventoryProfile(product_id=product_id)
        db.session.add(profile)
        db.session.flush()
    return profile


def update_inventory_profile(product_id: int, barcode: str | None, reorder_level: int, shelf_location: str | None = None):
    profile = _profile_for(product_id)
    profile.barcode = (barcode or "").strip() or None
    profile.reorder_level = max(0, int(reorder_level or 0))
    profile.shelf_location = (shelf_location or "").strip() or None
    db.session.commit()
    return profile


def search_product_by_barcode(barcode: str) -> Product | None:
    profile = db.session.execute(
        db.select(ProductInventoryProfile).where(ProductInventoryProfile.barcode == barcode.strip())
    ).scalar_one_or_none()
    if profile:
        return db.session.get(Product, profile.product_id)
    return None


def create_batches_for_purchase(purchase: Purchase) -> None:
    for item in purchase.items:
        existing = db.session.execute(
            db.select(ProductBatch).where(
                ProductBatch.purchase_id == purchase.id,
                ProductBatch.product_id == item.product_id,
            )
        ).scalars().first()
        if existing:
            continue
        product = db.session.get(Product, item.product_id)
        db.session.add(
            ProductBatch(
                product_id=item.product_id,
                purchase_id=purchase.id,
                batch_code=f"P{purchase.id:05d}-PR{item.product_id}",
                quantity_received=item.quantity,
                quantity_remaining=item.quantity,
                expiry_date=getattr(product, "expiry_date", None),
            )
        )


def get_credit_records() -> list[dict]:
    sales = db.session.execute(
        db.select(Sale).where(Sale.payment_mode == "credit").order_by(Sale.sale_date.desc())
    ).scalars().all()
    records = []
    for sale in sales:
        paid = db.session.execute(
            db.select(func.coalesce(func.sum(CustomerCreditPayment.amount), 0)).where(
                CustomerCreditPayment.sale_id == sale.id
            )
        ).scalar() or 0
        outstanding = max(Decimal("0.00"), _money(sale.total_amount) - _money(paid))
        records.append(
            {
                "sale": sale,
                "paid_amount": float(_money(paid)),
                "outstanding_amount": float(outstanding),
                "payments": db.session.execute(
                    db.select(CustomerCreditPayment)
                    .where(CustomerCreditPayment.sale_id == sale.id)
                    .order_by(CustomerCreditPayment.paid_at.desc())
                ).scalars().all(),
            }
        )
    return records


def record_credit_payment(sale_id: int, user_id: int, amount: float, payment_mode: str, note: str | None = None):
    sale = db.get_or_404(Sale, sale_id)
    amount_decimal = _money(amount)
    if amount_decimal <= 0:
        raise ValueError("Payment amount must be greater than zero.")

    outstanding = next(r for r in get_credit_records() if r["sale"].id == sale_id)["outstanding_amount"]
    if amount_decimal > _money(outstanding):
        raise ValueError("Payment amount cannot exceed the outstanding balance.")

    db.session.add(
        CustomerCreditPayment(
            sale_id=sale_id,
            user_id=user_id,
            amount=amount_decimal,
            payment_mode=(payment_mode or "cash").strip().lower(),
            note=(note or "").strip() or None,
        )
    )
    new_balance = _money(outstanding) - amount_decimal
    sale.credit_collected = new_balance <= Decimal("0.00")
    db.session.commit()


def get_supplier_balances() -> list[dict]:
    suppliers = db.session.execute(db.select(Supplier).order_by(Supplier.name)).scalars().all()
    rows = []
    for supplier in suppliers:
        purchases = db.session.execute(
            db.select(Purchase).where(Purchase.supplier_id == supplier.id).order_by(Purchase.purchase_date.desc())
        ).scalars().all()
        total_purchases = sum(_money(p.total_cost) for p in purchases)
        total_paid = db.session.execute(
            db.select(func.coalesce(func.sum(SupplierPayment.amount), 0)).where(
                SupplierPayment.supplier_id == supplier.id
            )
        ).scalar() or 0
        rows.append(
            {
                "supplier": supplier,
                "purchases": purchases,
                "total_purchases": float(total_purchases),
                "total_paid": float(_money(total_paid)),
                "outstanding": float(max(Decimal("0.00"), total_purchases - _money(total_paid))),
                "payments": db.session.execute(
                    db.select(SupplierPayment)
                    .where(SupplierPayment.supplier_id == supplier.id)
                    .order_by(SupplierPayment.paid_at.desc())
                ).scalars().all(),
            }
        )
    return rows


def record_supplier_payment(
    supplier_id: int,
    user_id: int,
    amount: float,
    payment_mode: str,
    purchase_id: int | None = None,
    note: str | None = None,
):
    amount_decimal = _money(amount)
    if amount_decimal <= 0:
        raise ValueError("Payment amount must be greater than zero.")
    row = next(r for r in get_supplier_balances() if r["supplier"].id == supplier_id)
    if amount_decimal > _money(row["outstanding"]):
        raise ValueError("Payment amount cannot exceed the supplier outstanding balance.")
    db.session.add(
        SupplierPayment(
            supplier_id=supplier_id,
            purchase_id=purchase_id,
            user_id=user_id,
            amount=amount_decimal,
            payment_mode=(payment_mode or "cash").strip().lower(),
            note=(note or "").strip() or None,
        )
    )
    db.session.commit()


def get_open_cash_session(user_id: int) -> CashSession | None:
    return db.session.execute(
        db.select(CashSession).where(CashSession.user_id == user_id, CashSession.status == "open")
    ).scalar_one_or_none()


def open_cash_session(user_id: int, opening_cash: float, notes: str | None = None) -> CashSession:
    if get_open_cash_session(user_id):
        raise ValueError("You already have an open cash session.")
    session = CashSession(
        user_id=user_id,
        opening_cash=_money(opening_cash),
        notes=(notes or "").strip() or None,
    )
    db.session.add(session)
    db.session.commit()
    return session


def close_cash_session(session_id: int, closing_cash: float, notes: str | None = None) -> CashSession:
    session = db.get_or_404(CashSession, session_id)
    if session.status != "open":
        raise ValueError("This cash session is already closed.")

    cash_sales = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0)).where(
            Sale.user_id == session.user_id,
            Sale.payment_mode == "cash",
            Sale.sale_date >= session.opened_at,
        )
    ).scalar() or 0
    session_day = session.opened_at.date()
    expenses = db.session.execute(
        db.select(func.coalesce(func.sum(Expense.amount), 0)).where(Expense.expense_date == session_day)
    ).scalar() or 0

    expected_cash = _money(session.opening_cash) + _money(cash_sales) - _money(expenses)
    actual_cash = _money(closing_cash)
    session.expected_cash = expected_cash
    session.closing_cash = actual_cash
    session.variance = actual_cash - expected_cash
    session.closed_at = datetime.now(timezone.utc)
    session.status = "closed"
    if notes:
        session.notes = (session.notes or "") + ("\n" if session.notes else "") + notes.strip()
    db.session.commit()
    return session


def get_reorder_suggestions(days: int = 30) -> list[dict]:
    cutoff = date.today() - timedelta(days=days)
    sales_by_product = defaultdict(int)
    sale_rows = db.session.execute(
        db.select(SaleItem.product_id, func.coalesce(func.sum(SaleItem.quantity), 0))
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= cutoff)
        .group_by(SaleItem.product_id)
    ).all()
    for product_id, qty in sale_rows:
        sales_by_product[product_id] = int(qty or 0)

    suggestions = []
    products = db.session.execute(db.select(Product).order_by(Product.name)).scalars().all()
    for product in products:
        profile = _profile_for(product.id)
        sold_recently = sales_by_product[product.id]
        daily_avg = sold_recently / days if days else 0
        suggested_qty = max(0, int((daily_avg * 14) + profile.reorder_level - product.quantity))
        if product.quantity <= profile.reorder_level or suggested_qty > 0:
            suggestions.append(
                {
                    "product": product,
                    "profile": profile,
                    "sold_recently": sold_recently,
                    "suggested_qty": suggested_qty,
                }
            )
    suggestions.sort(key=lambda row: (row["product"].quantity - row["profile"].reorder_level, row["product"].name))
    return suggestions


def award_loyalty_points(customer_name: str | None, sale_id: int, total_amount: float) -> None:
    customer_name = (customer_name or "").strip()
    if not customer_name or customer_name.lower() == "walk-in customer":
        return
    points = int(_money(total_amount) // Decimal("100"))
    if points <= 0:
        return
    db.session.add(
        CustomerLoyaltyTransaction(
            customer_name=customer_name,
            sale_id=sale_id,
            points_change=points,
            reason="sale_reward",
        )
    )


def get_loyalty_summary() -> list[dict]:
    rows = db.session.execute(
        db.select(
            CustomerLoyaltyTransaction.customer_name,
            func.coalesce(func.sum(CustomerLoyaltyTransaction.points_change), 0).label("points"),
            func.count(CustomerLoyaltyTransaction.id).label("entries"),
        )
        .group_by(CustomerLoyaltyTransaction.customer_name)
        .order_by(func.sum(CustomerLoyaltyTransaction.points_change).desc())
    ).all()
    return [{"customer_name": r.customer_name, "points": int(r.points), "entries": r.entries} for r in rows]


def ensure_notifications() -> list[AppNotification]:
    notifications: list[tuple[str, str, str, str, int]] = []
    for record in get_credit_records():
        if record["outstanding_amount"] > 0:
            notifications.append(
                (
                    "Overdue credit" if record["sale"].credit_due_date and record["sale"].credit_due_date < date.today() else "Pending credit",
                    f"Sale #{record['sale'].id} has NPR {record['outstanding_amount']:.2f} pending.",
                    "warning",
                    "sale",
                    record["sale"].id,
                )
            )
    for supplier_row in get_supplier_balances():
        if supplier_row["outstanding"] > 0:
            notifications.append(
                (
                    "Supplier balance due",
                    f"{supplier_row['supplier'].name} is owed NPR {supplier_row['outstanding']:.2f}.",
                    "info",
                    "supplier",
                    supplier_row["supplier"].id,
                )
            )
    for suggestion in get_reorder_suggestions():
        if suggestion["suggested_qty"] > 0:
            notifications.append(
                (
                    "Reorder recommended",
                    f"{suggestion['product'].name}: reorder about {suggestion['suggested_qty']} units.",
                    "danger" if suggestion["product"].quantity <= suggestion["profile"].reorder_level else "warning",
                    "product",
                    suggestion["product"].id,
                )
            )

    existing = {
        (n.title, n.body, n.source_type, n.source_id)
        for n in db.session.execute(db.select(AppNotification)).scalars().all()
    }
    created = False
    for title, body, category, source_type, source_id in notifications:
        key = (title, body, source_type, source_id)
        if key not in existing:
            db.session.add(
                AppNotification(
                    title=title,
                    body=body,
                    category=category,
                    source_type=source_type,
                    source_id=source_id,
                )
            )
            created = True
    if created:
        db.session.commit()
    return db.session.execute(db.select(AppNotification).order_by(AppNotification.created_at.desc())).scalars().all()


def mark_notification_read(notification_id: int) -> None:
    notification = db.get_or_404(AppNotification, notification_id)
    notification.is_read = True
    db.session.commit()


def list_branches() -> list[Branch]:
    return db.session.execute(db.select(Branch).order_by(Branch.name)).scalars().all()


def create_branch(name: str, code: str, address: str | None = None) -> Branch:
    branch = Branch(name=name.strip(), code=code.strip().upper(), address=(address or "").strip() or None)
    db.session.add(branch)
    db.session.commit()
    return branch


def export_backup_snapshot() -> bytes:
    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sales": [
            {
                "id": sale.id,
                "invoice_number": sale.invoice_number,
                "total_amount": float(sale.total_amount),
                "sale_date": sale.sale_date.isoformat() if sale.sale_date else None,
                "payment_mode": sale.payment_mode,
                "customer_name": sale.customer_name,
            }
            for sale in db.session.execute(db.select(Sale).order_by(Sale.id)).scalars().all()
        ],
        "purchases": [
            {
                "id": purchase.id,
                "supplier_id": purchase.supplier_id,
                "purchase_date": purchase.purchase_date.isoformat() if purchase.purchase_date else None,
                "total_cost": float(purchase.total_cost),
            }
            for purchase in db.session.execute(db.select(Purchase).order_by(Purchase.id)).scalars().all()
        ],
        "products": [
            {
                "id": product.id,
                "name": product.name,
                "sku": product.sku,
                "quantity": product.quantity,
                "cost_price": float(product.cost_price),
                "selling_price": float(product.selling_price),
            }
            for product in db.session.execute(db.select(Product).order_by(Product.id)).scalars().all()
        ],
        "customers": [
            {
                "id": customer.id,
                "name": customer.name,
                "phone": customer.phone,
                "address": customer.address,
            }
            for customer in db.session.execute(db.select(Customer).order_by(Customer.id)).scalars().all()
        ],
    }
    return json.dumps(snapshot, indent=2).encode("utf-8")
