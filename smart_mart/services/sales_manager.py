"""Sales management service — create sales, invoices, and stock reduction."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from io import BytesIO

from sqlalchemy import and_

from ..extensions import db
from ..models.product import Product
from ..models.sale import Sale, SaleItem
from ..models.stock_movement import StockMovement

logger = logging.getLogger(__name__)


def _lock_invoice_counter() -> None:
    """Serialize invoice-number generation across concurrent sales.

    Takes a row-level lock on the single shop_settings row, so two sales that
    reach _generate_invoice_number() must queue — their MAX() read and matching
    INSERT cannot interleave. The lock is released when the sale transaction
    commits or rolls back.

    Product row locks are NOT sufficient for this: two carts with no products
    in common lock disjoint rows, and an all-custom cart locks nothing at all.
    Both would read the same MAX() and mint the same invoice number.

    with_for_update() is a no-op on SQLite, which serializes writers with a
    database-level write lock instead; on PostgreSQL (production) it is a real
    row lock. Settings updates are rare admin actions, so the contention cost
    of borrowing that row as the sequence lock is negligible.
    """
    from ..models.shop_settings import ShopSettings
    settings_id = db.session.execute(db.select(db.func.min(ShopSettings.id))).scalar()
    if settings_id is None:
        # First ever sale on a fresh DB — creating the row is itself serialized
        # by the insert, and there is no prior invoice number to collide with.
        ShopSettings.get()
        return
    db.session.execute(
        db.select(ShopSettings.id)
        .where(ShopSettings.id == settings_id)
        .with_for_update()
    ).scalar()


def _generate_invoice_number() -> str:
    """Generate invoice number in format INV-YYYYMMDD-XXXX.

    Uses MAX() on existing committed invoice numbers for the day. Callers must
    hold the invoice counter lock (_lock_invoice_counter) so concurrent sales
    cannot both read the same MAX value. sales.invoice_number also carries a
    unique index as a backstop, so a collision fails loudly instead of silently
    storing a duplicate.

    Falls back gracefully on any DB error — the sale is more important than
    the invoice number format.
    """
    from datetime import date as _date
    today_str = _date.today().strftime("%Y%m%d")
    prefix = f"INV-{today_str}-"
    max_invoice = db.session.execute(
        db.select(db.func.max(Sale.invoice_number)).where(
            Sale.invoice_number.like(f"{prefix}%")
        )
    ).scalar()
    if max_invoice:
        try:
            last_seq = int(max_invoice.split("-")[-1])
        except (ValueError, IndexError):
            last_seq = 0
    else:
        last_seq = 0

    # Skip past any number already taken. The shop_settings lock and the unique
    # index on sales.invoice_number both make a collision unlikely, but neither
    # covers the case where MAX() misreads a hand-edited or imported number that
    # does not sort last, and a duplicate here fails the whole sale at INSERT.
    for _ in range(50):
        last_seq += 1
        candidate = f"{prefix}{last_seq:04d}"
        taken = db.session.execute(
            db.select(Sale.id).where(Sale.invoice_number == candidate).limit(1)
        ).scalar()
        if not taken:
            return candidate
    raise RuntimeError(f"Could not allocate an invoice number for {prefix}")


class InsufficientStockError(ValueError):
    """Raised when a sale item quantity exceeds available product stock."""


def invoice_payment_status(sale: Sale) -> dict:
    """How much of this sale has actually been collected.

    Returns ``{"status", "paid", "due", "is_credit"}`` where status is one of
    Paid / Partially Paid / Unpaid.

    Both the PDF invoice and the print-friendly HTML invoice used to decide this
    for themselves — the PDF hardcoded "Paid" and a green "Payment Received"
    banner, so every Udharo customer walked out holding a tax invoice stating
    the shop had already been paid in full; the HTML version keyed off
    payment_mode alone and so kept saying "Payment Pending" long after the
    credit had been collected. Deriving it in one place keeps the two documents
    from disagreeing about the same sale.
    """
    is_credit = "credit" in {
        (sale.payment_mode or "").strip().lower(),
        (sale.payment_method or "").strip().lower(),
    }
    total = float(sale.total_amount or 0)
    paid = total
    if is_credit and not sale.credit_collected:
        collected = 0.0
        try:
            from ..models.operations import CustomerCreditPayment
            collected = float(db.session.execute(
                db.select(db.func.coalesce(db.func.sum(CustomerCreditPayment.amount), 0))
                .where(CustomerCreditPayment.sale_id == sale.id)
            ).scalar() or 0)
        except Exception as exc:
            logger.warning("Credit payment lookup failed for sale %s: %s", sale.id, exc)
        paid = min(collected, total)
    due = round(max(0.0, total - paid), 2)
    if due <= 0:
        status = "Paid"
    elif paid > 0:
        status = "Partially Paid"
    else:
        status = "Unpaid"
    return {"status": status, "paid": round(paid, 2), "due": due, "is_credit": is_credit}


def invoice_vat_breakdown(sale: Sale) -> dict:
    """VAT split for an invoice, taken from the snapshot stored on the sale.

    Returns ``{"enabled", "rate", "vat", "taxable"}``.

    create_sale records tax_rate/tax_amount on the Sale. Recomputing them from
    ShopSettings at print time meant every historical tax invoice silently
    changed whenever the VAT rate was edited or VAT was toggled off, so a
    reprint no longer matched the copy the customer was originally handed.

    Sales predating the tax columns and sales rung up while VAT was off both
    carry a zero snapshot and cannot be told apart in the data, so a zero
    snapshot is taken at face value and those invoices show Tax (0%). The total
    is unaffected either way — VAT here is extracted from a tax-inclusive
    total, so this only changes how that total is broken down.
    """
    total = float(sale.total_amount or 0)
    rate = float(sale.tax_rate or 0)
    vat = float(sale.tax_amount or 0)
    enabled = rate > 0
    return {
        "enabled": enabled,
        "rate": rate,
        "vat": vat,
        "taxable": round(total - vat, 2) if enabled else total,
    }


def _customer_outstanding_credit(phone: str) -> float:
    """Unpaid credit balance for a customer.

    Sums uncollected credit sales and subtracts the partial payments recorded
    in customer_credit_payments — the same basis operations_manager
    .list_credit_records uses for the credit dashboard.

    The subtraction matters because record_credit_payment only flips
    Sale.credit_collected once the balance reaches zero, so a partly-paid sale
    still counts as uncollected. Without it a customer who has paid down most
    of a large credit sale is charged against their limit at the full original
    invoice value and gets wrongly blocked.
    """
    from ..models.operations import CustomerCreditPayment

    billed = db.session.execute(
        db.select(db.func.coalesce(db.func.sum(Sale.total_amount), 0))
        .where(Sale.customer_phone == phone)
        .where(Sale.payment_mode == "credit")
        .where(Sale.credit_collected == False)
    ).scalar() or 0
    paid = db.session.execute(
        db.select(db.func.coalesce(db.func.sum(CustomerCreditPayment.amount), 0))
        .join(Sale, Sale.id == CustomerCreditPayment.sale_id)
        .where(Sale.customer_phone == phone)
        .where(Sale.payment_mode == "credit")
        .where(Sale.credit_collected == False)
    ).scalar() or 0
    return max(0.0, float(billed) - float(paid))


def _enforce_credit_limit(customer_phone: str, sale_total: float) -> None:
    """Raise ValueError if this credit sale would push the customer over limit.

    Call this once, after sale_total is final (post discount and post loyalty
    redemption) — an earlier check would compare the limit against a total the
    customer is not actually being billed.
    """
    from ..models.customer import Customer

    phone = str(customer_phone).strip()
    customer = db.session.execute(
        db.select(Customer).where(Customer.phone == phone)
    ).scalar_one_or_none()
    if not customer or not customer.credit_limit or float(customer.credit_limit) <= 0:
        return

    outstanding = _customer_outstanding_credit(phone)
    if outstanding + float(sale_total) > float(customer.credit_limit):
        raise ValueError(
            f"Credit limit exceeded for {customer.name}. "
            f"Limit: NPR {float(customer.credit_limit):,.0f}, "
            f"Outstanding: NPR {outstanding:,.0f}, "
            f"This sale: NPR {float(sale_total):,.0f}."
        )


def create_sale(items: list[dict], user_id: int,
                customer_name: str = None, customer_address: str = None,
                customer_phone: str = None, payment_mode: str = "cash",
                payment_method: str | None = None,
                discount_amount: float = 0, discount_note: str = None,
                wallet_redeem_points: int = 0,
                promotion_id: int = None,
                applied_customer_offer_ids: list[int] | None = None,
                credit_due_date=None,
                sale_type: str = "regular") -> Sale:
    """Create a confirmed sale with row-level locking to prevent overselling."""
    try:
        # ── Period lock guard ─────────────────────────────────────────────
        from datetime import date as _date
        _today = _date.today()
        try:
            from ..models.financial_period import FinancialPeriod
            if FinancialPeriod.is_locked(_today.year, _today.month):
                raise ValueError(
                    f"The current period ({_today.strftime('%B %Y')}) is locked. "
                    "Contact an administrator to reopen it before posting new sales."
                )
        except ValueError:
            raise
        except Exception:
            pass  # Don't block sales if period check fails

        # ── Credit limit enforcement ───────────────────────────────────────
        # Enforced once, further down, by _enforce_credit_limit() — after
        # total_amount is final. A second pre-check used to run here on an
        # estimated total that ignored loyalty redemption and keyed off the raw
        # payment_mode instead of the normalized payment method, so the two
        # checks could disagree about the same sale.

        invoice_number = None

        # ── Resolve variant items → parent product ────────────────────────
        # Items from the billing page may carry variant_id instead of product_id
        # (encoded as "v:<id>" in the form, parsed to {"variant_id": id} in
        # _parse_items). Resolve each one to its parent product so the rest of
        # the sale logic works unchanged.
        variant_items: dict[int, object] = {}  # variant_id → ProductVariant
        if any(item.get("variant_id") for item in items):
            from ..models.product_variant import ProductVariant as _PV
            variant_ids = [item["variant_id"] for item in items if item.get("variant_id")]
            _variants = db.session.execute(
                db.select(_PV).where(_PV.id.in_(variant_ids)).with_for_update()
            ).scalars().all()
            variant_items = {v.id: v for v in _variants}
            # Back-fill product_id so downstream code can use items uniformly
            for item in items:
                vid = item.get("variant_id")
                if vid:
                    v = variant_items.get(vid)
                    if not v:
                        raise ValueError(f"Variant id {vid} not found.")
                    item["product_id"] = v.product_id

        # ── Separate custom/loose items (product_id=None, no stock to lock) ─
        custom_items = [item for item in items if item.get("product_id") is None and not item.get("variant_id")]
        regular_items = [item for item in items if item.get("product_id") is not None or item.get("variant_id")]

        # ── Lock product rows to prevent concurrent oversell ──────────────
        # SELECT ... FOR UPDATE acquires a row-level lock so two concurrent
        # sales of the same product cannot both pass the stock check.
        product_ids = list({item["product_id"] for item in regular_items if item.get("product_id")})
        locked_products = db.session.execute(
            db.select(Product)
            .where(Product.id.in_(product_ids))
            .with_for_update()
        ).scalars().all() if product_ids else []
        products: dict[int, Product] = {p.id: p for p in locked_products}

        # Generate the invoice number under the counter lock so two concurrent
        # sales cannot read the same MAX value. Acquired after the variant and
        # product locks so every caller takes locks in the same order.
        try:
            _lock_invoice_counter()
            invoice_number = _generate_invoice_number()
        except Exception as exc:
            logger.warning("Invoice number generation failed; using fallback: %s", exc)

        for item in regular_items:
            pid = item["product_id"]
            if pid not in products:
                raise ValueError(f"Product with id {pid} not found.")
            vid = item.get("variant_id")
            if vid:
                # Check variant stock, not parent product stock
                v = variant_items[vid]
                if item["quantity"] > v.quantity:
                    product = products[pid]
                    raise InsufficientStockError(
                        f"Insufficient stock for '{product.name} – {v.variant_name}': "
                        f"requested {item['quantity']}, available {v.quantity}."
                    )
            else:
                product = products[pid]
                if item["quantity"] > product.quantity:
                    raise InsufficientStockError(
                        f"Insufficient stock for '{product.name}': "
                        f"requested {item['quantity']}, available {product.quantity}."
                    )

        gross_total_amount = sum(item["unit_price"] * item["quantity"] for item in items)

        # ── Discount sanity ───────────────────────────────────────────────
        # discount_amount comes straight off the POS form. Clamping the result
        # with max(0, ...) silently accepted a discount larger than the cart,
        # which zeroed the sale while leaving Sale.discount_amount inconsistent
        # with Sale.total_amount in every profit and discount-leak report.
        # Reject it instead — the route surfaces ValueError to the cashier.
        # The 0.01 tolerance matches _mark_customer_offers_used and exists only
        # so a legitimate discount-to-zero isn't rejected on float rounding.
        discount_value = float(discount_amount or 0)
        if discount_value < 0:
            raise ValueError("Discount cannot be negative.")
        if discount_value > gross_total_amount + 0.01:
            raise ValueError(
                f"Discount NPR {discount_value:,.2f} exceeds the cart total of "
                f"NPR {gross_total_amount:,.2f}."
            )
        total_amount = max(0, gross_total_amount - discount_value)
        normalized_payment = (payment_method or payment_mode or "cash").strip().lower()

        redeemed_points = 0
        loyalty_discount_amount = 0.0
        wallet = None
        from . import loyalty_wallet_service
        wallet = loyalty_wallet_service.get_or_create_wallet(customer_name, customer_phone)
        if wallet_redeem_points:
            redeem_preview = loyalty_wallet_service.preview_redeem(
                wallet, int(wallet_redeem_points or 0), total_amount
            )
            redeemed_points = int(redeem_preview["redeemed_points"])
            loyalty_discount_amount = float(redeem_preview["discount"])
            total_amount = redeem_preview["payable_total"]
            if redeemed_points > 0:
                if discount_note:
                    discount_note = f"{discount_note}; Loyalty points redeemed"
                else:
                    discount_note = "Loyalty points redeemed"
        # ── Credit limit ──────────────────────────────────────────────────
        # Checked here, where total_amount is final (post discount, post
        # loyalty redemption). Infrastructure errors stay non-blocking — a
        # failed query should not cost the shop a sale — but they are logged
        # loudly rather than swallowed, because a silently skipped check is
        # otherwise indistinguishable from a passed one.
        if normalized_payment == "credit" and customer_phone:
            try:
                _enforce_credit_limit(customer_phone, total_amount)
            except ValueError:
                raise
            except Exception as exc:
                logger.warning(
                    "Credit limit check could not be completed for %s — sale allowed: %s",
                    customer_phone, exc,
                )

        # ── Fetch VAT settings for tax snapshot ──────────────────────────
        _tax_rate = 0.0
        _tax_amount = 0.0
        try:
            from ..models.shop_settings import ShopSettings
            _ss = ShopSettings.get()
            if _ss.vat_enabled and _ss.vat_rate:
                _tax_rate = float(_ss.vat_rate)
                # VAT is inclusive — extract from total
                _tax_amount = round(total_amount * _tax_rate / (100 + _tax_rate), 2)
        except Exception:
            pass

        # Auto-calculate staff commission
        commission_amount = 0.0
        try:
            from ..models.user import User as _User
            staff = db.session.get(_User, user_id)
            if staff and staff.commission_rate:
                commission_amount = float(total_amount) * float(staff.commission_rate) / 100.0
        except Exception:
            pass

        sale = Sale(
            user_id=user_id,
            total_amount=total_amount,
            sale_date=datetime.now(timezone.utc),
            invoice_number=invoice_number,
            customer_name=customer_name,
            customer_address=customer_address,
            customer_phone=customer_phone,
            payment_mode=normalized_payment,
            payment_method=normalized_payment,
            sale_type=sale_type or "regular",
            discount_amount=discount_value + loyalty_discount_amount,
            commission_amount=commission_amount,
            discount_note=discount_note,
            promotion_id=promotion_id,
            tax_rate=_tax_rate,
            tax_amount=_tax_amount,
            credit_due_date=credit_due_date,
        )
        db.session.add(sale)
        db.session.flush()

        for item in items:
            pid = item.get("product_id")
            qty = Decimal(str(item["quantity"]))
            unit_price = Decimal(str(item["unit_price"]))
            vid = item.get("variant_id")

            # ── Custom / loose item — no product FK, no stock deduction ───
            if pid is None and vid is None:
                db.session.add(SaleItem(
                    sale_id=sale.id,
                    product_id=None,
                    custom_label=item.get("custom_label") or "Custom Item",
                    quantity=qty,
                    unit_price=unit_price,
                    cost_price=None,
                    subtotal=unit_price * qty,
                ))
                continue

            product = products[pid]
            cost = product.cost_price
            movement_note = None
            if vid and vid in variant_items:
                v = variant_items[vid]
                cost = v.cost_price
                from ..utils.qty import qty_sub as _qty_sub
                _qty_sub(v, qty)   # deduct variant stock
                # stock_movements is keyed by product_id only, so a variant sale
                # is logged against the parent product. Name the variant in the
                # note so the ledger row can still be traced to what moved.
                movement_note = f"Variant: {v.variant_name} ({v.sku})"
            else:
                from ..utils.qty import qty_sub as _qty_sub
                _qty_sub(product, qty)  # deduct parent product stock
            db.session.add(SaleItem(
                sale_id=sale.id, product_id=product.id,
                variant_id=vid if vid else None,
                custom_label=None,
                quantity=qty, unit_price=unit_price,
                cost_price=cost,        # snapshot cost at time of sale
                subtotal=unit_price * qty,
            ))
            db.session.add(StockMovement(
                product_id=product.id, change_amount=-qty, change_type="sale",
                reference_id=sale.id, created_by=user_id,
                note=movement_note,
                timestamp=datetime.now(timezone.utc),
            ))

        try:
            from . import cash_flow_manager
            cash_flow_manager.record_income(sale)
        except ImportError:
            pass

        # Save customer for autofill on next visit
        try:
            from ..models.customer import Customer
            if customer_name and customer_name.strip().lower() != "walk-in customer":
                Customer.upsert(customer_name, customer_phone, customer_address)
                db.session.flush()
                # Auto-populate customer_id FK — use phone for precise match,
                # fall back to name only if no phone provided
                cust = None
                if customer_phone and customer_phone.strip():
                    cust = db.session.execute(
                        db.select(Customer).where(Customer.phone == customer_phone.strip())
                    ).scalar_one_or_none()
                if cust is None:
                    # Name + phone match
                    q = db.select(Customer).where(
                        db.func.lower(Customer.name) == customer_name.strip().lower()
                    )
                    if customer_phone and customer_phone.strip():
                        q = q.where(Customer.phone == customer_phone.strip())
                    cust = db.session.execute(q).scalar_one_or_none()
                if cust:
                    sale.customer_id = cust.id
                    cust.total_spent = (cust.total_spent or Decimal("0")) + Decimal(str(total_amount))
                    # Update loyalty tier based on total_spent (wallet handles actual points)
                    ts = cust.total_spent
                    if ts >= 100000:
                        cust.loyalty_tier = "platinum"
                    elif ts >= 50000:
                        cust.loyalty_tier = "gold"
                    elif ts >= 25000:
                        cust.loyalty_tier = "silver"
                    else:
                        cust.loyalty_tier = "bronze"
        except Exception as e:
            logger.warning("Customer upsert failed: %s", e)

        applied_offer_discount = _mark_customer_offers_used(
            sale=sale,
            items=items,
            posted_discount=discount_value,
            customer_offer_ids=applied_customer_offer_ids or [],
        )
        if applied_offer_discount > 0:
            offer_note = f"Customer offer discount NPR {applied_offer_discount:.2f}"
            sale.discount_note = f"{discount_note}; {offer_note}" if discount_note else offer_note
            sale.discount_note = sale.discount_note[:120]

        if wallet is not None:
            loyalty_wallet_service.apply_sale_points(
                wallet=wallet,
                sale_id=sale.id,
                final_amount_paid=total_amount,
                redeemed_points=redeemed_points,
            )

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    # Audit log (outside transaction)
    try:
        from . import audit_service
        audit_service.log("create", "Sale", sale.id,
                          f"Invoice {sale.invoice_number or sale.id}",
                          changes={"total_amount": [None, str(sale.total_amount)],
                                   "payment_method": [None, sale.payment_method]})
        db.session.commit()
    except Exception as exc:
        logger.warning("Sale audit logging failed for sale %s: %s", sale.id, exc)

    # Invalidate BI dashboard cache so next load reflects the new sale
    try:
        from .cache_service import invalidate_prefix
        invalidate_prefix("bi_dashboard")
        invalidate_prefix("alert_count:")  # reset alert badge cache
    except Exception:
        pass

    # Send WhatsApp/SMS receipt to customer (non-blocking — failure never breaks the sale)
    try:
        if customer_phone and customer_phone.strip():
            from .notification_service import send_notification
            from ..models.shop_settings import ShopSettings
            shop = ShopSettings.get()
            inv = sale.invoice_number or str(sale.id)
            msg = (
                f"Thank you for shopping at {shop.shop_name}!\n"
                f"Invoice: {inv}\n"
                f"Amount: NPR {float(sale.total_amount):,.0f}\n"
                f"Payment: {sale.payment_method or sale.payment_mode}\n"
            )
            if sale.payment_mode == "credit":
                msg += f"Balance due: NPR {float(sale.total_amount):,.0f}\n"
            msg += "We appreciate your business!"
            send_notification(customer_phone.strip(), msg, channel="whatsapp")
    except Exception as exc:
        logger.debug("Post-sale WhatsApp receipt failed (non-critical): %s", exc)

    return sale


def _mark_customer_offers_used(
    sale: Sale,
    items: list[dict],
    posted_discount: float,
    customer_offer_ids: list[int],
) -> float:
    """Validate POS-submitted customer offers and mark them used in the sale transaction."""
    clean_ids: list[int] = []
    for raw_id in customer_offer_ids:
        try:
            customer_offer_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if customer_offer_id > 0 and customer_offer_id not in clean_ids:
            clean_ids.append(customer_offer_id)

    if not clean_ids:
        return 0.0
    if not sale.customer_id:
        raise ValueError("Applied offers require a saved customer.")

    from datetime import date
    from ..models.offer import CustomerOffer

    cart_total = sum(float(item["unit_price"]) * int(item["quantity"]) for item in items)
    product_subtotals: dict[int, float] = {}
    for item in items:
        pid = item.get("product_id")
        if pid is None:
            continue   # custom/loose items have no product_id — skip for per-product offer calc
        product_id = int(pid)
        product_subtotals[product_id] = (
            product_subtotals.get(product_id, 0.0)
            + float(item["unit_price"]) * int(item["quantity"])
        )

    today = date.today()
    total_offer_discount = 0.0
    labels: list[str] = []

    for customer_offer_id in clean_ids:
        co = db.session.get(CustomerOffer, customer_offer_id)
        if not co:
            raise ValueError("Selected customer offer was not found.")
        if co.customer_id != sale.customer_id:
            raise ValueError("Selected offer does not belong to this customer.")
        if co.status == CustomerOffer.STATUS_USED:
            raise ValueError(f"Offer '{co.offer.title}' has already been used.")
        if co.status == CustomerOffer.STATUS_EXPIRED or co.expiry_date < today:
            co.status = CustomerOffer.STATUS_EXPIRED
            raise ValueError(f"Offer '{co.offer.title}' has expired.")

        offer = co.offer
        if co.usage_count >= offer.usage_limit:
            raise ValueError(f"Offer '{offer.title}' usage limit reached.")
        if not offer.is_active:
            raise ValueError(f"Offer '{offer.title}' is not active.")

        product_subtotal = product_subtotals.get(offer.product_id, 0.0) if offer.product_id else 0.0
        offer_discount = float(offer.calculate_discount(cart_total, product_subtotal))
        if offer_discount <= 0:
            raise ValueError(f"Offer '{offer.title}' is not applicable to this cart.")

        total_offer_discount += offer_discount
        labels.append(offer.title)
        co.status = CustomerOffer.STATUS_USED
        co.usage_count += 1
        co.applied_sale_id = sale.id

    if total_offer_discount > posted_discount + 0.01:
        raise ValueError("Offer discount is larger than the submitted sale discount.")

    if labels:
        logger.info("Applied customer offers to sale #%d: %s", sale.id, ", ".join(labels))
    return round(total_offer_discount, 2)


def get_sale(sale_id: int) -> Sale:
    return db.get_or_404(Sale, sale_id)


def count_sales(filters: dict) -> int:
    """Return total count of sales matching filters (for pagination)."""
    from sqlalchemy import or_
    stmt = db.select(db.func.count(Sale.id))
    conditions = []
    if filters.get("start_date"):
        conditions.append(Sale.sale_date >= filters["start_date"])
    if filters.get("end_date"):
        conditions.append(Sale.sale_date <= filters["end_date"])
    if filters.get("payment_mode"):
        conditions.append(or_(
            Sale.payment_method == filters["payment_mode"],
            Sale.payment_mode == filters["payment_mode"],
        ))
    if filters.get("search"):
        term = f"%{filters['search'].lower()}%"
        conditions.append(or_(
            db.func.lower(Sale.customer_name).like(term),
            db.func.lower(Sale.customer_phone).like(term),
            db.func.lower(Sale.invoice_number).like(term),
        ))
    if conditions:
        stmt = stmt.where(and_(*conditions))
    return db.session.execute(stmt).scalar() or 0


def list_sales(filters: dict, page: int = 1, per_page: int = 20) -> list[Sale]:
    from sqlalchemy import or_
    from sqlalchemy.orm import joinedload
    stmt = (
        db.select(Sale)
        .options(joinedload(Sale.user))   # avoid N+1 on cashier name in the list view
        .order_by(Sale.sale_date.desc())
    )
    conditions = []
    if filters.get("start_date"):
        conditions.append(Sale.sale_date >= filters["start_date"])
    if filters.get("end_date"):
        conditions.append(Sale.sale_date <= filters["end_date"])
    if filters.get("payment_mode"):
        conditions.append(or_(
            Sale.payment_method == filters["payment_mode"],
            Sale.payment_mode == filters["payment_mode"],
        ))
    if filters.get("search"):
        term = f"%{filters['search'].lower()}%"
        conditions.append(or_(
            db.func.lower(Sale.customer_name).like(term),
            db.func.lower(Sale.customer_phone).like(term),
            db.func.lower(Sale.invoice_number).like(term),
        ))
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = stmt.limit(per_page).offset((page - 1) * per_page)
    return db.session.execute(stmt).unique().scalars().all()


def delete_sale(sale_id: int) -> None:
    """Delete a sale and return its stock to the bucket it came out of.

    Variant lines are credited back to the ProductVariant, not the parent
    Product. create_sale deducts variant stock for those lines, so crediting
    the parent here invented parent units that never existed while the sold
    variant units stayed lost — every void of a variant sale drifted inventory
    in both directions at once.

    Units already restored by a sale return are netted off. Deleting the sale
    cascades its SaleReturn rows away, so this is the last point at which they
    can be accounted for.
    """
    from ..models.product_variant import ProductVariant
    from ..models.sale_return import SaleReturnItem

    sale = get_sale(sale_id)
    try:
        for item in sale.items:
            if item.product_id is None and item.variant_id is None:
                continue  # custom/loose item — no stock to reverse

            already_returned = db.session.execute(
                db.select(db.func.coalesce(db.func.sum(SaleReturnItem.quantity), 0))
                .where(SaleReturnItem.sale_item_id == item.id)
            ).scalar() or 0
            from decimal import Decimal as _D
            restore_qty = _D(str(item.quantity)) - _D(str(already_returned))
            if restore_qty <= 0:
                continue  # fully returned already — stock is back on the shelf

            if item.variant_id:
                variant = db.session.get(ProductVariant, item.variant_id)
                if variant is None:
                    # The FK is ON DELETE SET NULL, so a live variant_id pointing
                    # at a missing row should not happen. Skip rather than guess —
                    # crediting the parent would corrupt its count.
                    logger.warning(
                        "Sale #%s item %s references missing variant %s — "
                        "stock not reversed.", sale.id, item.id, item.variant_id,
                    )
                    continue
                variant.quantity = _D(str(variant.quantity or 0)) + restore_qty
                db.session.add(StockMovement(
                    product_id=variant.product_id,
                    change_amount=float(restore_qty),
                    change_type="adjustment_in",
                    reference_id=sale.id,
                    note=(f"Sale #{sale.id} deleted/reversed — variant: "
                          f"{variant.variant_name} ({variant.sku})"),
                    created_by=sale.user_id,
                    timestamp=datetime.now(timezone.utc),
                ))
                continue

            product = db.session.get(Product, item.product_id)
            if product:
                product.quantity = _D(str(product.quantity or 0)) + restore_qty
                db.session.add(StockMovement(
                    product_id=product.id,
                    change_amount=float(restore_qty),
                    change_type="adjustment_in",
                    reference_id=sale.id,
                    note=f"Sale #{sale.id} deleted/reversed",
                    created_by=sale.user_id,
                    timestamp=datetime.now(timezone.utc),
                ))

        # Reverse loyalty points earned on this sale
        try:
            from .loyalty_wallet_service import reverse_sale_points
            reverse_sale_points(sale.id)
        except Exception as e:
            logger.warning("Loyalty reversal failed for sale %s: %s", sale.id, e)

        # Rollback any offer applied to this sale
        try:
            from .offer_service import rollback_offer
            rollback_offer(sale.id)
        except Exception as e:
            logger.warning("Offer rollback failed for sale %s: %s", sale.id, e)

        # Reverse the customer's total_spent so loyalty tiers don't drift upward
        # from voided/deleted sales.
        if sale.customer_id:
            from ..models.customer import Customer
            cust = db.session.get(Customer, sale.customer_id)
            if cust:
                sale_total = float(sale.total_amount or 0)
                cust.total_spent = max(0.0, float(cust.total_spent or 0) - sale_total)
                # Recalculate loyalty tier after reversal
                ts = cust.total_spent
                if ts >= 100000:
                    cust.loyalty_tier = "platinum"
                elif ts >= 50000:
                    cust.loyalty_tier = "gold"
                elif ts >= 20000:
                    cust.loyalty_tier = "silver"
                else:
                    cust.loyalty_tier = "bronze"

        db.session.delete(sale)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def generate_invoice_pdf(sale_id: int) -> bytes:
    """Generate a professional Tax Invoice PDF using ReportLab."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                     Paragraph, Spacer, HRFlowable)
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT

    sale: Sale = get_sale(sale_id)
    buffer = BytesIO()

    # ── Shop settings ─────────────────────────────────────────────────────
    shop_name = "Goldkernel Dryfruits and Treats"
    shop_pan = shop_address = shop_phone = shop_email = ""
    footer_note = "Thank you for your business!"
    shop_logo_path = None
    try:
        from ..models.shop_settings import ShopSettings
        s = ShopSettings.get()
        shop_name = s.shop_name or shop_name
        shop_pan = s.pan_number or ""
        shop_address = s.address or ""
        shop_phone = s.phone or ""
        shop_email = s.email or ""
        footer_note = s.footer_note or footer_note
        if s.logo_data:
            # Use base64 data from DB (works on Render)
            shop_logo_path = s.logo_data  # will be handled as data URI
        elif s.logo_filename:
            import os
            from flask import current_app
            logo_path = os.path.join(current_app.static_folder, "uploads", "shop", s.logo_filename)
            if os.path.exists(logo_path):
                shop_logo_path = logo_path
    except Exception:
        pass

    invoice_num = sale.invoice_number or f"INV-{sale.id:05d}"
    customer_name = sale.customer_name or "Walk-in Customer"
    customer_address = sale.customer_address or ""
    customer_phone = sale.customer_phone or ""
    pm = (sale.payment_mode or "cash").upper()
    pm_labels = {"CASH": "Cash", "QR": "QR / Digital Wallet",
                 "CARD": "Card", "CREDIT": "Credit / Udharo", "OTHER": "Other"}
    payment_label = pm_labels.get(pm, pm)

    # ── Payment status — never assume the invoice is paid ──────────────────
    _pay = invoice_payment_status(sale)
    payment_status = _pay["status"]
    amount_paid = _pay["paid"]
    amount_due = _pay["due"]

    # ── Document setup ────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm
    )
    W = A4[0] - 3.6*cm

    # Colors
    navy = colors.HexColor("#1e3a5f")
    blue = colors.HexColor("#2563eb")
    light_blue = colors.HexColor("#eff6ff")
    slate = colors.HexColor("#64748b")
    border = colors.HexColor("#cbd5e1")
    green = colors.HexColor("#16a34a")
    light_green = colors.HexColor("#f0fdf4")
    red = colors.HexColor("#dc2626")
    light_red = colors.HexColor("#fef2f2")
    row_alt = colors.HexColor("#f8fafc")

    def S(name, **kw):
        return ParagraphStyle(name, **kw)

    story = []

    # ── HEADER: two-column layout ─────────────────────────────────────────
    # Left column: logo + shop name + details
    # Right column: TAX INVOICE + invoice number + date
    left_col = []

    # Add logo if available
    if shop_logo_path:
        try:
            from reportlab.platypus import Image as RLImage
            import io as _io
            if shop_logo_path.startswith("data:"):
                # base64 data URI — decode to bytes
                import base64
                header, b64data = shop_logo_path.split(",", 1)
                img_bytes = base64.b64decode(b64data)
                logo_img = RLImage(_io.BytesIO(img_bytes), width=3*cm, height=1.5*cm, kind='proportional')
            else:
                logo_img = RLImage(shop_logo_path, width=3*cm, height=1.5*cm, kind='proportional')
            left_col.append(logo_img)
            left_col.append(Spacer(1, 4))
        except Exception:
            pass

    left_col.append(
        Paragraph(f"<b>{shop_name}</b>",
                  S("sn", fontSize=16, fontName="Helvetica-Bold",
                    textColor=navy, spaceAfter=4, leading=20))
    )
    contact_parts = []
    if shop_address:
        contact_parts.append(shop_address)
    if shop_phone:
        contact_parts.append(f"Tel: {shop_phone}")
    if shop_email:
        contact_parts.append(shop_email)
    if shop_pan:
        contact_parts.append(f"PAN No: {shop_pan}")
    try:
        from ..models.shop_settings import ShopSettings as _SS
        _sv = _SS.get()
        if _sv.vat_enabled and _sv.vat_number:
            contact_parts.append(f"VAT No: {_sv.vat_number}")
    except Exception:
        pass
    for part in contact_parts:
        left_col.append(Paragraph(part, S(f"sc_{part[:5]}", fontSize=8,
                                          fontName="Helvetica", textColor=slate,
                                          spaceAfter=2, leading=11)))

    right_col = [
        Paragraph("TAX INVOICE",
                  S("ti", fontSize=16, fontName="Helvetica-Bold",
                    textColor=blue, alignment=TA_RIGHT, spaceAfter=5, leading=20)),
        Paragraph(f"<b>No: {invoice_num}</b>",
                  S("inv", fontSize=10, fontName="Helvetica-Bold",
                    textColor=navy, alignment=TA_RIGHT, spaceAfter=3, leading=13)),
        Paragraph((sale.sale_date + timedelta(hours=5, minutes=45)).strftime("%d %B %Y  %I:%M %p") if sale.sale_date else "",
                  S("dt", fontSize=8, fontName="Helvetica",
                    textColor=slate, alignment=TA_RIGHT, leading=11)),
    ]

    hdr = Table([[left_col, right_col]], colWidths=[W * 0.55, W * 0.45])
    hdr.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), light_blue),
        ("LEFTPADDING", (0, 0), (0, 0), 14),
        ("RIGHTPADDING", (0, 0), (0, 0), 10),
        ("LEFTPADDING", (1, 0), (1, 0), 10),
        ("RIGHTPADDING", (1, 0), (1, 0), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
        ("LINEBELOW", (0, 0), (-1, -1), 2.5, blue),
    ]))
    story.append(hdr)
    story.append(Spacer(1, 0.4 * cm))

    # ── BILL TO + PAYMENT DETAILS ─────────────────────────────────────────
    lbl_s = S("lbl", fontSize=7, fontName="Helvetica-Bold", textColor=slate,
               spaceAfter=3, leading=10)
    val_s = S("val", fontSize=9, fontName="Helvetica", textColor=navy,
               spaceAfter=2, leading=12)
    val_b = S("valb", fontSize=9, fontName="Helvetica-Bold", textColor=navy,
               spaceAfter=2, leading=12)

    bill_items = [Paragraph("BILL TO", lbl_s),
                  Paragraph(customer_name, val_b)]
    if customer_phone:
        bill_items.append(Paragraph(f"Tel: {customer_phone}", val_s))
    if customer_address:
        bill_items.append(Paragraph(customer_address, val_s))

    pay_items = [
        Paragraph("PAYMENT DETAILS", lbl_s),
        Paragraph(f"Mode:  <b>{payment_label}</b>", val_s),
        Paragraph(f"Status:  <b>{payment_status}</b>", val_s),
        Paragraph(f"Served by:  <b>{sale.user.username if sale.user else '—'}</b>", val_s),
    ]
    if amount_due > 0 and sale.credit_due_date:
        pay_items.append(Paragraph(
            f"Due by:  <b>{sale.credit_due_date.strftime('%d %b %Y')}</b>", val_s))

    meta = Table([[bill_items, pay_items]], colWidths=[W * 0.52, W * 0.48])
    meta.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (0, 0), 0.5, border),
        ("BOX", (1, 0), (1, 0), 0.5, border),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(meta)
    story.append(Spacer(1, 0.4 * cm))

    # ── ITEMS TABLE ───────────────────────────────────────────────────────
    col_w = [0.7*cm, 5.8*cm, 1.4*cm, 1.4*cm, 2.4*cm, 1.8*cm, 2.5*cm]
    th = S("th", fontSize=8, fontName="Helvetica-Bold",
            textColor=colors.white, alignment=TA_CENTER)
    th_r = S("thr", fontSize=8, fontName="Helvetica-Bold",
              textColor=colors.white, alignment=TA_RIGHT)

    rows = [[
        Paragraph("#", th),
        Paragraph("DESCRIPTION", th),
        Paragraph("UNIT", th),
        Paragraph("QTY", th),
        Paragraph("RATE (NPR)", th_r),
        Paragraph("DISC", th_r),
        Paragraph("AMOUNT (NPR)", th_r),
    ]]

    for i, si in enumerate(sale.items, 1):
        name = si.custom_label or (si.product.name if si.product else "Custom Item")
        sku = si.product.sku if si.product else ""
        unit = (getattr(si.product, "unit", None) if si.product else None) or "pcs"
        desc = [
            Paragraph(f"<b>{name}</b>",
                      S(f"pn{i}", fontSize=9, fontName="Helvetica-Bold",
                        textColor=navy, leading=12)),
            Paragraph(f"SKU: {sku}",
                      S(f"sk{i}", fontSize=7, fontName="Helvetica",
                        textColor=slate, leading=10)),
        ]
        rows.append([
            Paragraph(str(i), S(f"n{i}", fontSize=9, fontName="Helvetica",
                                 textColor=slate, alignment=TA_CENTER)),
            desc,
            Paragraph(unit, S(f"u{i}", fontSize=9, fontName="Helvetica",
                               textColor=slate, alignment=TA_CENTER)),
            Paragraph(str(si.quantity), S(f"q{i}", fontSize=9, fontName="Helvetica",
                                           textColor=navy, alignment=TA_CENTER)),
            Paragraph(f"{float(si.unit_price):,.2f}",
                      S(f"up{i}", fontSize=9, fontName="Helvetica",
                        textColor=navy, alignment=TA_RIGHT)),
            Paragraph("—", S(f"d{i}", fontSize=9, fontName="Helvetica",
                              textColor=slate, alignment=TA_RIGHT)),
            Paragraph(f"{float(si.subtotal):,.2f}",
                      S(f"st{i}", fontSize=9, fontName="Helvetica-Bold",
                        textColor=navy, alignment=TA_RIGHT)),
        ])

    row_bgs = [("BACKGROUND", (0, i), (-1, i),
                colors.white if i % 2 == 1 else row_alt)
               for i in range(1, len(rows))]

    it = Table(rows, colWidths=col_w, repeatRows=1)
    it.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), navy),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
        ("LINEBELOW", (0, 0), (-1, 0), 1.5, blue),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("TOPPADDING", (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.3, border),
    ] + row_bgs))
    story.append(it)
    story.append(Spacer(1, 0.3 * cm))

    # ── TOTALS ────────────────────────────────────────────────────────────
    gross_total = sum(float(si.subtotal) for si in sale.items)
    discount = float(sale.discount_amount or 0)
    final_total = float(sale.total_amount)

    # ── VAT — from the snapshot on the sale, not from current settings ────
    _vat = invoice_vat_breakdown(sale)
    vat_enabled = _vat["enabled"]
    vat_rate = _vat["rate"]
    vat_amount = _vat["vat"]
    taxable_amount = _vat["taxable"]

    tot_s = S("ts", fontSize=9, fontName="Helvetica", textColor=slate)
    tot_v = S("tv", fontSize=9, fontName="Helvetica", textColor=navy, alignment=TA_RIGHT)
    tot_red = S("tr", fontSize=9, fontName="Helvetica", textColor=red, alignment=TA_RIGHT)

    totals_rows = [
        [Paragraph("Sub Total:", tot_s), Paragraph(f"NPR {gross_total:,.2f}", tot_v)],
    ]
    if discount > 0:
        disc_label = f"Discount ({sale.discount_note}):" if sale.discount_note else "Discount:"
        totals_rows.append([Paragraph(disc_label, tot_s), Paragraph(f"- NPR {discount:,.2f}", tot_red)])
    if vat_enabled:
        totals_rows.append([Paragraph("Taxable Amount:", tot_s), Paragraph(f"NPR {taxable_amount:,.2f}", tot_v)])
        totals_rows.append([Paragraph(f"VAT ({vat_rate:.0f}%):", tot_s), Paragraph(f"NPR {vat_amount:,.2f}", tot_v)])
    else:
        totals_rows.append([Paragraph("Tax (0%):", tot_s), Paragraph("NPR 0.00", tot_v)])

    totals = Table(totals_rows, colWidths=[W * 0.8, W * 0.2])
    totals.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(totals)

    grand = Table([[
        Paragraph("TOTAL AMOUNT",
                  S("gt", fontSize=11, fontName="Helvetica-Bold",
                    textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph(f"NPR {final_total:,.2f}",
                  S("gv", fontSize=12, fontName="Helvetica-Bold",
                    textColor=colors.white, alignment=TA_RIGHT)),
    ]], colWidths=[W * 0.75, W * 0.25])
    grand.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), navy),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(grand)
    story.append(Spacer(1, 0.4 * cm))

    # ── PAYMENT CONFIRMATION / AMOUNT DUE ─────────────────────────────────
    # Green "Payment Received" only when the money is actually in. An open
    # credit sale gets a red amount-due panel instead, so the invoice doubles
    # as the collection record the cashier hands over.
    if amount_due > 0:
        conf_label = "Balance Due" if amount_paid > 0 else "Amount Due"
        conf_extra = f"  (Paid: NPR {amount_paid:,.2f})" if amount_paid > 0 else ""
        conf_amount = amount_due
        conf_fg, conf_bg = red, light_red
    else:
        conf_label = "Payment Received"
        conf_extra = ""
        conf_amount = amount_paid
        conf_fg, conf_bg = green, light_green

    pay_conf = Table([[
        Paragraph(f"{conf_label}  —  <b>{payment_label}</b>{conf_extra}",
                  S("pc", fontSize=9, fontName="Helvetica-Bold", textColor=conf_fg)),
        Paragraph(f"NPR {conf_amount:,.2f}",
                  S("pv", fontSize=11, fontName="Helvetica-Bold",
                    textColor=conf_fg, alignment=TA_RIGHT)),
    ]], colWidths=[W * 0.72, W * 0.28])
    pay_conf.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), conf_bg),
        ("BOX", (0, 0), (-1, -1), 1, conf_fg),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(pay_conf)
    story.append(Spacer(1, 0.5 * cm))

    # ── FOOTER ────────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=border, spaceAfter=5))
    story.append(Paragraph(footer_note,
                            S("fn", fontSize=8, fontName="Helvetica",
                              textColor=slate, alignment=TA_CENTER)))
    story.append(Paragraph(
        "This is a computer-generated Tax Invoice. No signature required.",
        S("fn2", fontSize=7, fontName="Helvetica",
          textColor=colors.HexColor("#94a3b8"), alignment=TA_CENTER)
    ))

    doc.build(story)
    return buffer.getvalue()
