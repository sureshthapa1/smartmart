"""Property-based tests for purchase management.

Properties covered:
  Property 11: Transaction totals equal the sum of their line items (purchases)
  Property 12: Purchase confirmation increases product stock correctly
"""
# Feature: smart-mart-inventory

from decimal import Decimal

import pytest
@pytest.mark.slow`nfrom hypothesis import given, settings
from hypothesis import strategies as st

from smart_mart.app import create_app
from smart_mart.extensions import db as _db
from smart_mart.models.product import Product
from smart_mart.models.supplier import Supplier
from smart_mart.models.user import User
from smart_mart.services import purchase_manager


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


def _seed(sku, quantity=10):
    import uuid
    supplier = Supplier(name=f"Sup-{uuid.uuid4().hex[:6]}")
    _db.session.add(supplier)
    _db.session.flush()
    product = Product(
        name=f"Prod-{sku}",
        category="Test",
        sku=sku,
        cost_price=Decimal("40.00"),
        selling_price=Decimal("70.00"),
        quantity=quantity,
        supplier_id=supplier.id,
    )
    _db.session.add(product)
    user = User(username=f"u-{uuid.uuid4().hex[:6]}", password_hash="h", role="admin")
    _db.session.add(user)
    _db.session.commit()
    return product, user, supplier


# ── Property 12: Purchase confirmation increases product stock correctly ───────

@settings(max_examples=100)
@given(qty=st.integers(min_value=1, max_value=100))
def test_purchase_increases_stock(app, qty):
    # Feature: smart-mart-inventory, Property 12: Purchase confirmation increases product stock correctly
    with app.app_context():
        import uuid
        from datetime import date
        product, user, supplier = _seed(f"PUR-{uuid.uuid4().hex[:8]}", quantity=5)
        initial_qty = product.quantity
        purchase_manager.create_purchase(
            supplier_id=supplier.id,
            items=[{"product_id": product.id, "quantity": qty, "unit_cost": 40.0}],
            purchase_date=date.today(),
            user_id=user.id,
        )
        refreshed = _db.session.get(Product, product.id)
        assert refreshed.quantity == initial_qty + qty


# ── Property 11: Purchase totals equal sum of line items ─────────────────────

@settings(max_examples=100)
@given(
    items=st.lists(
        st.tuples(
            st.integers(min_value=1, max_value=20),
            st.floats(min_value=1.0, max_value=200.0, allow_nan=False, allow_infinity=False),
        ),
        min_size=1,
        max_size=5,
    )
)
def test_purchase_total_equals_sum_of_line_items(app, items):
    # Feature: smart-mart-inventory, Property 11: Transaction totals equal the sum of their line items (purchases)
    with app.app_context():
        import uuid
        from datetime import date
        user = User(username=f"u-{uuid.uuid4().hex[:6]}", password_hash="h", role="admin")
        _db.session.add(user)
        supplier = Supplier(name=f"Sup-{uuid.uuid4().hex[:6]}")
        _db.session.add(supplier)
        _db.session.flush()

        purchase_items = []
        expected_total = 0.0
        for qty, cost in items:
            cost = round(cost, 2)
            product = Product(
                name=f"P-{uuid.uuid4().hex[:6]}",
                category="T",
                sku=f"SKU-{uuid.uuid4().hex[:8]}",
                cost_price=Decimal(str(cost)),
                selling_price=Decimal(str(cost * 1.5)),
                quantity=0,
                supplier_id=supplier.id,
            )
            _db.session.add(product)
            _db.session.flush()
            purchase_items.append({"product_id": product.id, "quantity": qty, "unit_cost": cost})
            expected_total += round(qty * cost, 2)

        _db.session.commit()
        purchase = purchase_manager.create_purchase(
            supplier_id=supplier.id,
            items=purchase_items,
            purchase_date=date.today(),
            user_id=user.id,
        )
        assert abs(float(purchase.total_cost) - round(expected_total, 2)) < 0.02

