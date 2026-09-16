"""Regression tests for what an invoice says about payment and VAT.

The PDF invoice hardcoded "Paid" plus a green "Payment Received" banner, so a
credit/Udharo customer received a tax invoice stating the shop had already been
paid in full. The print-friendly HTML invoice had the mirror-image bug: it keyed
off payment_mode alone, so a credit sale kept reading "Payment Pending" after
the money was collected. Both now come from the same two helpers.
"""

from decimal import Decimal

import pytest

from smart_mart.extensions import db as _db
from smart_mart.models.product import Product
from smart_mart.models.shop_settings import ShopSettings
from smart_mart.models.user import User
from smart_mart.services import operations_manager, sales_manager


def _user(username: str = "invoicer") -> User:
    user = User(username=username, password_hash="hash", role="admin")
    _db.session.add(user)
    _db.session.commit()
    return user


def _product(sku: str = "INV-TEA") -> Product:
    product = Product(
        name="Masala Tea",
        sku=sku,
        category="Beverages",
        cost_price=Decimal("60.00"),
        selling_price=Decimal("100.00"),
        quantity=100,
    )
    _db.session.add(product)
    _db.session.commit()
    return product


def _sale(user, product, qty=5, **kwargs):
    return sales_manager.create_sale(
        [{"product_id": product.id, "quantity": qty, "unit_price": 100}],
        user_id=user.id,
        **kwargs,
    )


# ── Payment status ────────────────────────────────────────────────────────

def test_cash_sale_reads_as_paid(db):
    sale = _sale(_user(), _product(sku="INV-CASH"))

    pay = sales_manager.invoice_payment_status(sale)
    assert pay["status"] == "Paid"
    assert pay["due"] == 0.0
    assert pay["paid"] == 500.0
    assert pay["is_credit"] is False


def test_uncollected_credit_sale_reads_as_unpaid(db):
    sale = _sale(
        _user("inv_credit"), _product(sku="INV-CR"),
        customer_name="Udharo Buyer", customer_phone="9800011111",
        payment_mode="credit",
    )

    pay = sales_manager.invoice_payment_status(sale)
    assert pay["status"] == "Unpaid"
    assert pay["paid"] == 0.0
    assert pay["due"] == 500.0
    assert pay["is_credit"] is True


def test_part_paid_credit_sale_reads_as_partially_paid(db):
    user = _user("inv_partial")
    sale = _sale(
        user, _product(sku="INV-PART"),
        customer_name="Udharo Buyer", customer_phone="9800022222",
        payment_mode="credit",
    )

    operations_manager.record_credit_payment(
        sale_id=sale.id, user_id=user.id, amount=200, payment_mode="cash"
    )

    pay = sales_manager.invoice_payment_status(sale)
    assert pay["status"] == "Partially Paid"
    assert pay["paid"] == 200.0
    assert pay["due"] == 300.0


def test_fully_collected_credit_sale_reads_as_paid(db):
    user = _user("inv_collected")
    sale = _sale(
        user, _product(sku="INV-COLL"),
        customer_name="Udharo Buyer", customer_phone="9800033333",
        payment_mode="credit",
    )

    operations_manager.record_credit_payment(
        sale_id=sale.id, user_id=user.id, amount=500, payment_mode="cash"
    )

    pay = sales_manager.invoice_payment_status(sale)
    assert pay["status"] == "Paid"
    assert pay["due"] == 0.0


# ── VAT snapshot ──────────────────────────────────────────────────────────

def test_vat_breakdown_uses_the_sale_snapshot_not_current_settings(db):
    """Editing the VAT rate must not rewrite invoices already issued."""
    settings = ShopSettings.get()
    settings.vat_enabled = True
    settings.vat_rate = Decimal("13.00")
    db.session.commit()

    sale = _sale(_user("inv_vat"), _product(sku="INV-VAT"))
    assert float(sale.tax_rate) == 13.0

    before = sales_manager.invoice_vat_breakdown(sale)
    assert before["enabled"] is True
    assert before["rate"] == 13.0
    # 500 inclusive of 13% → 57.52 VAT on a 442.48 taxable base.
    assert before["vat"] == pytest.approx(57.52, abs=0.01)
    assert before["taxable"] == pytest.approx(442.48, abs=0.01)

    # Shop switches VAT off, or the rate changes. The already-issued invoice
    # must reprint exactly as the customer received it.
    settings.vat_enabled = False
    settings.vat_rate = Decimal("0.00")
    db.session.commit()

    after = sales_manager.invoice_vat_breakdown(sale)
    assert after == before


def test_vat_breakdown_is_zero_when_sale_carries_no_tax(db):
    settings = ShopSettings.get()
    settings.vat_enabled = False
    settings.vat_rate = Decimal("0.00")
    db.session.commit()

    sale = _sale(_user("inv_novat"), _product(sku="INV-NOVAT"))

    vat = sales_manager.invoice_vat_breakdown(sale)
    assert vat["enabled"] is False
    assert vat["vat"] == 0.0
    assert vat["taxable"] == 500.0


# ── PDF smoke ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["cash", "credit"])
def test_invoice_pdf_renders_for_paid_and_unpaid_sales(db, mode):
    """Exercise both branches of the payment panel end to end."""
    user = _user(f"inv_pdf_{mode}")
    product = _product(sku=f"INV-PDF-{mode.upper()}")
    kwargs = {"payment_mode": mode}
    if mode == "credit":
        kwargs.update(customer_name="Udharo Buyer", customer_phone="9800044444")
    sale = _sale(user, product, **kwargs)

    pdf = sales_manager.generate_invoice_pdf(sale.id)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 1000
