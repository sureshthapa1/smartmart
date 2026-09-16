"""Regression tests for discount validation and credit-limit accounting.

Both used to be wrong in ways that only showed up in the money reports rather
than at the till:

- A discount larger than the cart was clamped to zero with max(0, ...), leaving
  Sale.discount_amount inconsistent with Sale.total_amount forever after.
- The credit-limit check compared the limit against the full invoice value of
  every uncollected credit sale, ignoring partial payments — so a customer who
  had paid most of a large Udharo bill down was still blocked at the counter.
"""

from decimal import Decimal

import pytest

from smart_mart.extensions import db as _db
from smart_mart.models.customer import Customer
from smart_mart.models.product import Product
from smart_mart.models.sale import Sale
from smart_mart.models.user import User
from smart_mart.services import operations_manager, sales_manager


def _user(username: str = "till") -> User:
    user = User(username=username, password_hash="hash", role="admin")
    _db.session.add(user)
    _db.session.commit()
    return user


def _product(sku: str = "DISC-RICE", quantity: int = 100) -> Product:
    product = Product(
        name="Basmati Rice",
        sku=sku,
        category="Grocery",
        cost_price=Decimal("60.00"),
        selling_price=Decimal("100.00"),
        quantity=quantity,
    )
    _db.session.add(product)
    # Commit, not flush: create_sale rolls back its whole transaction when it
    # rejects a sale, which would take an unflushed fixture down with it.
    _db.session.commit()
    return product


def _customer(phone: str = "9811111111", credit_limit=None) -> Customer:
    customer = Customer(name="Udharo Buyer", phone=phone, address="Dhangadhi")
    if credit_limit is not None:
        customer.credit_limit = credit_limit
    _db.session.add(customer)
    _db.session.flush()
    return customer


# ── Discount validation ───────────────────────────────────────────────────

def test_create_sale_rejects_discount_above_cart_total(db):
    user = _user()
    product = _product(sku="DISC-OVER")

    with pytest.raises(ValueError, match="exceeds the cart total"):
        sales_manager.create_sale(
            [{"product_id": product.id, "quantity": 2, "unit_price": 100}],
            user_id=user.id,
            discount_amount=500,
        )

    # Nothing committed: stock untouched and no sale row left behind.
    assert db.session.get(Product, product.id).quantity == 100
    assert db.session.execute(db.select(db.func.count(Sale.id))).scalar() == 0


def test_create_sale_rejects_negative_discount(db):
    user = _user("till_neg")
    product = _product(sku="DISC-NEG")

    with pytest.raises(ValueError, match="cannot be negative"):
        sales_manager.create_sale(
            [{"product_id": product.id, "quantity": 1, "unit_price": 100}],
            user_id=user.id,
            discount_amount=-50,
        )

    assert db.session.get(Product, product.id).quantity == 100


def test_create_sale_allows_full_cart_discount(db):
    """A 100% discount is a legitimate giveaway, not an error."""
    user = _user("till_full")
    product = _product(sku="DISC-FULL")

    sale = sales_manager.create_sale(
        [{"product_id": product.id, "quantity": 2, "unit_price": 100}],
        user_id=user.id,
        discount_amount=200,
    )

    assert float(sale.total_amount) == 0.0
    assert float(sale.discount_amount) == 200.0
    assert db.session.get(Product, product.id).quantity == 98


# ── Credit limit ──────────────────────────────────────────────────────────

def test_credit_limit_blocks_second_sale_over_headroom(db):
    user = _user("till_credit")
    product = _product(sku="CR-BLOCK")
    customer = _customer(phone="9822222222", credit_limit=1000)
    db.session.commit()

    sales_manager.create_sale(
        [{"product_id": product.id, "quantity": 8, "unit_price": 100}],
        user_id=user.id,
        customer_name=customer.name,
        customer_phone=customer.phone,
        payment_mode="credit",
    )

    # 800 outstanding, 1000 limit — a further 300 does not fit.
    with pytest.raises(ValueError, match="Credit limit exceeded"):
        sales_manager.create_sale(
            [{"product_id": product.id, "quantity": 3, "unit_price": 100}],
            user_id=user.id,
            customer_name=customer.name,
            customer_phone=customer.phone,
            payment_mode="credit",
        )


def test_credit_limit_counts_partial_payments_as_paid_down(db):
    """Money already collected has to free up headroom against the limit.

    record_credit_payment only flips Sale.credit_collected once the balance
    reaches zero, so a partly-paid sale is still 'uncollected'. Summing invoice
    totals alone charged the customer for money they had already handed over.
    """
    user = _user("till_partial")
    product = _product(sku="CR-PARTIAL")
    customer = _customer(phone="9833333333", credit_limit=1000)
    db.session.commit()

    first = sales_manager.create_sale(
        [{"product_id": product.id, "quantity": 9, "unit_price": 100}],
        user_id=user.id,
        customer_name=customer.name,
        customer_phone=customer.phone,
        payment_mode="credit",
    )
    assert float(first.total_amount) == 900.0

    # Customer pays 700 of the 900 — still not collected in full.
    operations_manager.record_credit_payment(
        sale_id=first.id, user_id=user.id, amount=700, payment_mode="cash"
    )
    assert db.session.get(Sale, first.id).credit_collected is False

    # Outstanding is now 200, so an 800 credit sale fits exactly inside the
    # 1000 limit. Before the fix this raised, because the check still counted
    # the first sale at its full 900.
    second = sales_manager.create_sale(
        [{"product_id": product.id, "quantity": 8, "unit_price": 100}],
        user_id=user.id,
        customer_name=customer.name,
        customer_phone=customer.phone,
        payment_mode="credit",
    )
    assert float(second.total_amount) == 800.0


def test_credit_limit_measured_after_discount(db):
    """The limit applies to what the customer is billed, not the cart value."""
    user = _user("till_disc_credit")
    product = _product(sku="CR-DISC")
    customer = _customer(phone="9844444444", credit_limit=500)
    db.session.commit()

    # 700 cart, 300 off — the 400 actually billed fits under the 500 limit.
    sale = sales_manager.create_sale(
        [{"product_id": product.id, "quantity": 7, "unit_price": 100}],
        user_id=user.id,
        customer_name=customer.name,
        customer_phone=customer.phone,
        payment_mode="credit",
        discount_amount=300,
    )

    assert float(sale.total_amount) == 400.0


# ── Invoice numbering ─────────────────────────────────────────────────────

def test_invoice_numbers_are_unique_and_sequential(db):
    user = _user("till_inv")
    product = _product(sku="INV-SEQ")

    numbers = [
        sales_manager.create_sale(
            [{"product_id": product.id, "quantity": 1, "unit_price": 100}],
            user_id=user.id,
        ).invoice_number
        for _ in range(5)
    ]

    assert all(numbers), "every sale should get an invoice number"
    assert len(set(numbers)) == 5, f"duplicate invoice numbers: {numbers}"
    assert numbers == sorted(numbers)


def test_invoice_number_generator_skips_a_taken_number(db):
    """A hand-edited or imported number must not be handed out twice.

    MAX() only finds the highest-sorting number, so a manually inserted one that
    collides with the next sequential value would otherwise be reused and fail
    the whole sale at INSERT.
    """
    user = _user("till_inv_skip")
    product = _product(sku="INV-SKIP")

    first = sales_manager.create_sale(
        [{"product_id": product.id, "quantity": 1, "unit_price": 100}],
        user_id=user.id,
    )
    prefix, seq = first.invoice_number.rsplit("-", 1)
    squatter = f"{prefix}-{int(seq) + 1:04d}"

    # Park the next sequential number on an unrelated sale, then bump the
    # original so MAX() no longer points at the squatter.
    first.invoice_number = squatter
    db.session.commit()

    second = sales_manager.create_sale(
        [{"product_id": product.id, "quantity": 1, "unit_price": 100}],
        user_id=user.id,
    )
    assert second.invoice_number != squatter
