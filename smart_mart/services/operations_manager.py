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


# ---------------------------------------------------------------------------
# Credit records — single aggregated query (fix #2: N+1)
# ---------------------------------------------------------------------------

def get_credit_records(page: int = 1, per_page: int = 50) -> dict:
    """Return paginated credit records with aggregated payment totals in 2 queries."""
    # Aggregate paid amounts per sale in one query
    paid_subq = (
        db.select(
            CustomerCreditPayment.sale_id,
            func.coalesce(func.sum(CustomerCreditPayment.amount), 0).label("paid"),
        )
        .group_by(CustomerCreditPayment.sale_id)
        .subquery()
    )

    total_count = db.session.execute(
        db.select(func.count(Sale.id)).where(Sale.payment_mode == "credit")
    ).scalar() or 0

    sales = db.session.execute(
        db.select(Sale, func.coalesce(paid_subq.c.paid, 0).label("paid"))
        .outerjoin(paid_subq, paid_subq.c.sale_id == Sale.id)
        .where(Sale.payment_mode == "credit")
        .order_by(Sale.sale_date.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
    ).all()

    # Fetch payments for visible sales only
    sale_ids = [row.Sale.id for row in sales]
    payments_by_sale: dict[int, list] = defaultdict(list)
    if sale_ids:
        for payment in db.session.execute(
            db.select(CustomerCreditPayment)
            .where(CustomerCreditPayment.sale_id.in_(sale_ids))
            .order_by(CustomerCreditPayment.paid_at.desc())
        ).scalars().all():
            payments_by_sale[payment.sale_id].append(payment)

    records = []
    for row in sales:
        sale = row.Sale
        paid = _money(row.paid)
        outstanding = max(Decimal("0.00"), _money(sale.total_amount) - paid)
        records.append({
            "sale": sale,
            "paid_amount": float(paid),
            "outstanding_amount": float(outstanding),
            "payments": payments_by_sale[sale.id],
        })

    return {
        "records": records,
        "total": total_count,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total_count + per_page - 1) // per_page),
    }


def record_credit_payment(sale_id: int, user_id: int, amount: float, payment_mode: str, note: str | None = None):
    sale = db.get_or_404(Sale, sale_id)
    amount_decimal = _money(amount)
    if amount_decimal <= 0:
        raise ValueError("Payment amount must be greater than zero.")

    paid = db.session.execute(
        db.select(func.coalesce(func.sum(CustomerCreditPayment.amount), 0))
        .where(CustomerCreditPayment.sale_id == sale_id)
    ).scalar() or 0
    outstanding = max(Decimal("0.00"), _money(sale.total_amount) - _money(paid))

    if amount_decimal > outstanding:
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
    new_balance = outstanding - amount_decimal
    sale.credit_collected = new_balance <= Decimal("0.00")
    db.session.commit()

    # Dismiss stale notification for this sale
    _dismiss_notification("sale", sale_id)


# ---------------------------------------------------------------------------
# Supplier balances — single aggregated query (fix #2: N+1)
# ---------------------------------------------------------------------------

def get_supplier_balances(page: int = 1, per_page: int = 50) -> dict:
    """Return paginated supplier balances with aggregated totals in 2 queries."""
    paid_subq = (
        db.select(
            SupplierPayment.supplier_id,
            func.coalesce(func.sum(SupplierPayment.amount), 0).label("paid"),
        )
        .group_by(SupplierPayment.supplier_id)
        .subquery()
    )
    purchase_subq = (
        db.select(
            Purchase.supplier_id,
            func.coalesce(func.sum(Purchase.total_cost), 0).label("total_purchases"),
        )
        .group_by(Purchase.supplier_id)
        .subquery()
    )

    total_count = db.session.execute(db.select(func.count(Supplier.id))).scalar() or 0

    rows = db.session.execute(
        db.select(
            Supplier,
            func.coalesce(purchase_subq.c.total_purchases, 0).label("total_purchases"),
            func.coalesce(paid_subq.c.paid, 0).label("total_paid"),
        )
        .outerjoin(purchase_subq, purchase_subq.c.supplier_id == Supplier.id)
        .outerjoin(paid_subq, paid_subq.c.supplier_id == Supplier.id)
        .order_by(Supplier.name)
        .limit(per_page)
        .offset((page - 1) * per_page)
    ).all()

    supplier_ids = [r.Supplier.id for r in rows]
    purchases_by_supplier: dict[int, list] = defaultdict(list)
    payments_by_supplier: dict[int, list] = defaultdict(list)
    if supplier_ids:
        for p in db.session.execute(
            db.select(Purchase)
            .where(Purchase.supplier_id.in_(supplier_ids))
            .order_by(Purchase.purchase_date.desc())
        ).scalars().all():
            purchases_by_supplier[p.supplier_id].append(p)
        for sp in db.session.execute(
            db.select(SupplierPayment)
            .where(SupplierPayment.supplier_id.in_(supplier_ids))
            .order_by(SupplierPayment.paid_at.desc())
        ).scalars().all():
            payments_by_supplier[sp.supplier_id].append(sp)

    result = []
    for row in rows:
        supplier = row.Supplier
        total_purchases = _money(row.total_purchases)
        total_paid = _money(row.total_paid)
        outstanding = max(Decimal("0.00"), total_purchases - total_paid)
        result.append({
            "supplier": supplier,
            "purchases": purchases_by_supplier[supplier.id],
            "total_purchases": float(total_purchases),
            "total_paid": float(total_paid),
            "outstanding": float(outstanding),
            "payments": payments_by_supplier[supplier.id],
        })

    return {
        "rows": result,
        "total": total_count,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total_count + per_page - 1) // per_page),
    }


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

    paid = db.session.execute(
        db.select(func.coalesce(func.sum(SupplierPayment.amount), 0))
        .where(SupplierPayment.supplier_id == supplier_id)
    ).scalar() or 0
    total_purchases = db.session.execute(
        db.select(func.coalesce(func.sum(Purchase.total_cost), 0))
        .where(Purchase.supplier_id == supplier_id)
    ).scalar() or 0
    outstanding = max(Decimal("0.00"), _money(total_purchases) - _money(paid))

    if amount_decimal > outstanding:
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

    # Dismiss stale notification for this supplier
    _dismiss_notification("supplier", supplier_id)


# ---------------------------------------------------------------------------
# Cash sessions (fix #3: expense scoped to session window)
# ---------------------------------------------------------------------------

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

    closed_at = datetime.now(timezone.utc)

    # Cash sales within the session window only (fix #3)
    cash_sales = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0)).where(
            Sale.user_id == session.user_id,
            Sale.payment_mode == "cash",
            Sale.sale_date >= session.opened_at,
            Sale.sale_date <= closed_at,
        )
    ).scalar() or 0

    # Expenses within the session window only (fix #3)
    expenses = db.session.execute(
        db.select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.expense_date >= session.opened_at.date(),
            Expense.expense_date <= closed_at.date(),
        )
    ).scalar() or 0

    expected_cash = _money(session.opening_cash) + _money(cash_sales) - _money(expenses)
    actual_cash = _money(closing_cash)
    session.expected_cash = expected_cash
    session.closing_cash = actual_cash
    session.variance = actual_cash - expected_cash
    session.closed_at = closed_at
    session.status = "closed"
    if notes:
        session.notes = (session.notes or "") + ("\n" if session.notes else "") + notes.strip()
    db.session.commit()
    return session


# ---------------------------------------------------------------------------
# Reorder suggestions — bulk profile load (fix #4: N+1 per product)
# ---------------------------------------------------------------------------

def get_reorder_suggestions(days: int = 30) -> list[dict]:
    cutoff = date.today() - timedelta(days=days)
    sales_by_product = defaultdict(int)
    for product_id, qty in db.session.execute(
        db.select(SaleItem.product_id, func.coalesce(func.sum(SaleItem.quantity), 0))
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= cutoff)
        .group_by(SaleItem.product_id)
    ).all():
        sales_by_product[product_id] = int(qty or 0)

    # Load all profiles in one query (fix #4)
    profiles_map: dict[int, ProductInventoryProfile] = {
        p.product_id: p
        for p in db.session.execute(db.select(ProductInventoryProfile)).scalars().all()
    }

    products = db.session.execute(db.select(Product).order_by(Product.name)).scalars().all()

    # Create missing profiles in bulk
    new_profiles = []
    for product in products:
        if product.id not in profiles_map:
            profile = ProductInventoryProfile(product_id=product.id)
            db.session.add(profile)
            new_profiles.append(profile)
    if new_profiles:
        db.session.flush()
        for p in new_profiles:
            profiles_map[p.product_id] = p

    suggestions = []
    for product in products:
        profile = profiles_map[product.id]
        sold_recently = sales_by_product[product.id]
        daily_avg = sold_recently / days if days else 0
        suggested_qty = max(0, int((daily_avg * 14) + profile.reorder_level - product.quantity))
        if product.quantity <= profile.reorder_level or suggested_qty > 0:
            suggestions.append({
                "product": product,
                "profile": profile,
                "sold_recently": sold_recently,
                "suggested_qty": suggested_qty,
            })
    suggestions.sort(key=lambda row: (row["product"].quantity - row["profile"].reorder_level, row["product"].name))
    return suggestions


# ---------------------------------------------------------------------------
# Loyalty — redeem via operations page
# ---------------------------------------------------------------------------

def redeem_loyalty_points(customer_name: str, points: int, note: str | None = None) -> int:
    """Deduct points for a customer via the wallet. Returns new balance."""
    customer_name = customer_name.strip()
    if points <= 0:
        raise ValueError("Redemption points must be greater than zero.")
    from ..models.customer import Customer
    from ..models.ai_enhancements import LoyaltyWallet
    from . import loyalty_wallet_service
    customer = db.session.execute(
        db.select(Customer).where(db.func.lower(Customer.name) == customer_name.lower())
    ).scalar_one_or_none()
    if not customer:
        raise ValueError(f"Customer '{customer_name}' not found.")
    wallet = db.session.execute(
        db.select(LoyaltyWallet).where(LoyaltyWallet.customer_id == customer.id)
    ).scalar_one_or_none()
    if not wallet or wallet.points_balance < points:
        available = int(wallet.points_balance) if wallet else 0
        raise ValueError(f"Insufficient points. Available: {available}.")
    loyalty_wallet_service.redeem_points_manual(wallet.id, points, note or "manual_redeem")
    return int(wallet.points_balance)


def _get_loyalty_balance(customer_name: str) -> int:
    from ..models.customer import Customer
    from ..models.ai_enhancements import LoyaltyWallet
    customer = db.session.execute(
        db.select(Customer).where(db.func.lower(Customer.name) == customer_name.strip().lower())
    ).scalar_one_or_none()
    if not customer:
        return 0
    wallet = db.session.execute(
        db.select(LoyaltyWallet).where(LoyaltyWallet.customer_id == customer.id)
    ).scalar_one_or_none()
    return int(wallet.points_balance) if wallet else 0


def get_loyalty_summary() -> list[dict]:
    from ..models.ai_enhancements import LoyaltyWallet, LoyaltyWalletTransaction
    from ..models.customer import Customer
    rows = db.session.execute(
        db.select(
            Customer.name.label("customer_name"),
            LoyaltyWallet.points_balance.label("points"),
            LoyaltyWallet.lifetime_points_earned.label("lifetime"),
            LoyaltyWallet.tier.label("tier"),
            func.count(LoyaltyWalletTransaction.id).label("entries"),
        )
        .join(LoyaltyWallet, LoyaltyWallet.customer_id == Customer.id)
        .outerjoin(LoyaltyWalletTransaction, LoyaltyWalletTransaction.wallet_id == LoyaltyWallet.id)
        .group_by(Customer.id, LoyaltyWallet.id)
        .order_by(LoyaltyWallet.points_balance.desc())
    ).all()
    return [
        {
            "customer_name": r.customer_name,
            "points": int(r.points or 0),
            "lifetime": int(r.lifetime or 0),
            "tier": r.tier or "Silver",
            "entries": r.entries,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Notifications — stale cleanup (fix #1)
# ---------------------------------------------------------------------------

def _dismiss_notification(source_type: str, source_id: int) -> None:
    """Remove resolved notifications so they don't linger."""
    db.session.execute(
        db.delete(AppNotification).where(
            AppNotification.source_type == source_type,
            AppNotification.source_id == source_id,
        )
    )
    db.session.commit()


def ensure_notifications() -> list[AppNotification]:
    """Rebuild live notifications, removing stale ones first."""
    # Collect what should exist right now
    live: list[tuple[str, str, str, str, int]] = []

    # Credits
    credit_data = get_credit_records(per_page=1000)
    for record in credit_data["records"]:
        if record["outstanding_amount"] > 0:
            overdue = record["sale"].credit_due_date and record["sale"].credit_due_date < date.today()
            live.append((
                "Overdue credit" if overdue else "Pending credit",
                f"Sale #{record['sale'].id} has NPR {record['outstanding_amount']:.2f} pending.",
                "warning",
                "sale",
                record["sale"].id,
            ))

    # Suppliers
    supplier_data = get_supplier_balances(per_page=1000)
    for row in supplier_data["rows"]:
        if row["outstanding"] > 0:
            live.append((
                "Supplier balance due",
                f"{row['supplier'].name} is owed NPR {row['outstanding']:.2f}.",
                "info",
                "supplier",
                row["supplier"].id,
            ))

    # Reorders
    for suggestion in get_reorder_suggestions():
        if suggestion["suggested_qty"] > 0:
            live.append((
                "Reorder recommended",
                f"{suggestion['product'].name}: reorder about {suggestion['suggested_qty']} units.",
                "danger" if suggestion["product"].quantity <= suggestion["profile"].reorder_level else "warning",
                "product",
                suggestion["product"].id,
            ))

    # Build lookup of what should exist: (source_type, source_id) -> (title, body, category)
    live_keys = {(st, sid): (title, body, cat) for title, body, cat, st, sid in live}

    # Remove notifications whose source is no longer active
    existing = db.session.execute(db.select(AppNotification)).scalars().all()
    for n in existing:
        key = (n.source_type, n.source_id)
        if key not in live_keys:
            db.session.delete(n)

    # Add new ones that don't exist yet
    existing_keys = {(n.source_type, n.source_id) for n in existing}
    for title, body, category, source_type, source_id in live:
        if (source_type, source_id) not in existing_keys:
            db.session.add(AppNotification(
                title=title,
                body=body,
                category=category,
                source_type=source_type,
                source_id=source_id,
            ))

    db.session.commit()
    return db.session.execute(
        db.select(AppNotification).order_by(AppNotification.created_at.desc())
    ).scalars().all()


def mark_notification_read(notification_id: int) -> None:
    notification = db.get_or_404(AppNotification, notification_id)
    notification.is_read = True
    db.session.commit()


def mark_all_notifications_read() -> None:
    db.session.execute(
        db.update(AppNotification).where(AppNotification.is_read == False).values(is_read=True)
    )
    db.session.commit()


# ---------------------------------------------------------------------------
# Branches — toggle active + edit (fix #5)
# ---------------------------------------------------------------------------

def list_branches() -> list[Branch]:
    return db.session.execute(db.select(Branch).order_by(Branch.name)).scalars().all()


def create_branch(name: str, code: str, address: str | None = None) -> Branch:
    branch = Branch(name=name.strip(), code=code.strip().upper(), address=(address or "").strip() or None)
    db.session.add(branch)
    db.session.commit()
    return branch


def update_branch(branch_id: int, name: str, code: str, address: str | None = None) -> Branch:
    branch = db.get_or_404(Branch, branch_id)
    branch.name = name.strip()
    branch.code = code.strip().upper()
    branch.address = (address or "").strip() or None
    db.session.commit()
    return branch


def toggle_branch_active(branch_id: int) -> Branch:
    branch = db.get_or_404(Branch, branch_id)
    branch.is_active = not branch.is_active
    db.session.commit()
    return branch


# ---------------------------------------------------------------------------
# Backup export — extended (fix #7)
# ---------------------------------------------------------------------------

def export_backup_snapshot() -> bytes:
    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sales": [
            {
                "id": s.id,
                "invoice_number": s.invoice_number,
                "total_amount": float(s.total_amount),
                "sale_date": s.sale_date.isoformat() if s.sale_date else None,
                "payment_mode": s.payment_mode,
                "customer_name": s.customer_name,
            }
            for s in db.session.execute(db.select(Sale).order_by(Sale.id)).scalars().all()
        ],
        "purchases": [
            {
                "id": p.id,
                "supplier_id": p.supplier_id,
                "purchase_date": p.purchase_date.isoformat() if p.purchase_date else None,
                "total_cost": float(p.total_cost),
            }
            for p in db.session.execute(db.select(Purchase).order_by(Purchase.id)).scalars().all()
        ],
        "products": [
            {
                "id": p.id,
                "name": p.name,
                "sku": p.sku,
                "quantity": p.quantity,
                "cost_price": float(p.cost_price),
                "selling_price": float(p.selling_price),
            }
            for p in db.session.execute(db.select(Product).order_by(Product.id)).scalars().all()
        ],
        "customers": [
            {
                "id": c.id,
                "name": c.name,
                "phone": c.phone,
                "address": c.address,
            }
            for c in db.session.execute(db.select(Customer).order_by(Customer.id)).scalars().all()
        ],
        "suppliers": [
            {
                "id": s.id,
                "name": s.name,
                "phone": getattr(s, "phone", None),
                "address": getattr(s, "address", None),
            }
            for s in db.session.execute(db.select(Supplier).order_by(Supplier.id)).scalars().all()
        ],
        "expenses": [
            {
                "id": e.id,
                "amount": float(e.amount),
                "category": getattr(e, "category", None),
                "note": getattr(e, "note", None),
                "expense_date": e.expense_date.isoformat() if e.expense_date else None,
            }
            for e in db.session.execute(db.select(Expense).order_by(Expense.id)).scalars().all()
        ],
        "loyalty_transactions": [
            {
                "id": lt.id,
                "customer_name": lt.customer_name,
                "sale_id": lt.sale_id,
                "points_change": lt.points_change,
                "reason": lt.reason,
                "created_at": lt.created_at.isoformat(),
            }
            for lt in db.session.execute(db.select(CustomerLoyaltyTransaction).order_by(CustomerLoyaltyTransaction.id)).scalars().all()
        ],
    }
    return json.dumps(snapshot, indent=2).encode("utf-8")
