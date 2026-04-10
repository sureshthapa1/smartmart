"""Property-based tests for sales management.

Properties covered:
  Property 9: Sale confirmation reduces product stock correctly
  Property 10: Sales with insufficient stock are rejected atomically
  Property 11: Transaction totals equal the sum of their line items (sales)
"""
# Feature: smart-mart-inventory

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from smart_mart.app import create_app
from smart_mart.extensions import db as _db
from smart_mart.models.product import Product
from smart_mart.models.supplier import Supplier
from smart_mart.models.user import User
from smart_mart.services import sales_manager


@pytest.fixture(scope="module")
def app():
    application = create_app("testing")
    with application.app_context():
        _db.create_all()
        yield application
        _db.drop_all()


@pytest.fixture(autouse=True)
def app_ctx(app):
    with app.app_context():
        yield


def _seed(sku, quantity=50, cost=50, price=80):
    import uuid
    supplier = Supplier(name=f"Sup-{uuid.uuid4().hex[:6]}")
    _db.session.add(supplier)
    _db.session.flush()
    product = Product(
        name=f"Prod-{sku}",
        category="Test",
        sku=sku,
        cost_price=Decimal(str(cost)),
        selling_price=Decimal(str(price)),
        quantity=quantity,
        supplier_id=supplier.id,
    )
    _db.session.add(product)
    user = User(username=f"u-{uuid.uuid4().hex[:6]}", password_hash="h", role="admin")
    _db.session.add(user)
    _db.session.commit()
    return product, user


# ── Property 9: Sale confirmation reduces product stock correctly ─────────────

@settings(max_examples=100)
@given(qty=st.integers(min_value=1, max_value=20))
def test_sale_reduces_stock(app, qty):
    # Feature: smart-mart-inventory, Property 9: Sale confirmation reduces product stock correctly
    with app.app_context():
        import uuid
        product, user = _seed(f"SALE-{uuid.uuid4().hex[:8]}", quantity=50)
        initial_qty = product.quantity
        sales_manager.create_sale(
            [{"product_id": product.id, "quantity": qty, "unit_price": float(product.selling_price)}],
            user_id=user.id,
        )
        refreshed = _db.session.get(Product, product.id)
        assert refreshed.quantity == initial_qty - qty


# ── Property 10: Sales with insufficient stock are rejected atomically ────────

@settings(max_examples=100)
@given(stock=st.integers(min_value=0, max_value=10), excess=st.integers(min_value=1, max_value=10))
def test_insufficient_stock_rejected_atomically(app, stock, excess):
    # Feature: smart-mart-inventory, Property 10: Sales with insufficient stock are rejected atomically
    with app.app_context():
        import uuid
        product, user = _seed(f"INSUF-{uuid.uuid4().hex[:8]}", quantity=stock)
        requested = stock + excess  # always exceeds stock
        with pytest.raises((ValueError, Exception)):
            sales_manager.create_sale(
                [{"product_id": product.id, "quantity": requested, "unit_price": 80.0}],
                user_id=user.id,
            )
        refreshed = _db.session.get(Product, product.id)
        assert refreshed.quantity == stock  # no partial reduction


# ── Property 11: Transaction totals equal the sum of their line items ─────────

@settings(max_examples=100)
@given(
    items=st.lists(
        st.tuples(
            st.integers(min_value=1, max_value=5),   # quantity
            st.floats(min_value=1.0, max_value=500.0, allow_nan=False, allow_infinity=False),  # unit_price
        ),
        min_size=1,
        max_size=5,
    )
)
def test_sale_total_equals_sum_of_line_items(app, items):
    # Feature: smart-mart-inventory, Property 11: Transaction totals equal the sum of their line items
    with app.app_context():
        import uuid
        user = User(username=f"u-{uuid.uuid4().hex[:6]}", password_hash="h", role="admin")
        _db.session.add(user)
        _db.session.flush()

        sale_items = []
        expected_total = 0.0
        for qty, price in items:
            price = round(price, 2)
            supplier = Supplier(name=f"S-{uuid.uuid4().hex[:4]}")
            _db.session.add(supplier)
            _db.session.flush()
            product = Product(
                name=f"P-{uuid.uuid4().hex[:6]}",
                category="T",
                sku=f"SKU-{uuid.uuid4().hex[:8]}",
                cost_price=Decimal("10.00"),
                selling_price=Decimal(str(price)),
                quantity=100,
                supplier_id=supplier.id,
            )
            _db.session.add(product)
            _db.session.flush()
            sale_items.append({"product_id": product.id, "quantity": qty, "unit_price": price})
            expected_total += round(qty * price, 2)

        _db.session.commit()
        sale = sales_manager.create_sale(sale_items, user_id=user.id)
        assert abs(float(sale.total_amount) - round(expected_total, 2)) < 0.02
