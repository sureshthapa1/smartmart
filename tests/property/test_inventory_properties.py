"""Property-based tests for inventory management.

Properties covered:
  Property 5: Unique constraint violations are rejected
  Property 6: Stock adjustment arithmetic is correct
  Property 7: Stock-out below zero is rejected
  Property 8: Every stock-changing operation creates a movement record
  Property 25: Product search returns only matching results
"""
# Feature: smart-mart-inventory

from decimal import Decimal

import pytest
@pytest.mark.slow`nfrom hypothesis import given, settings
from hypothesis import strategies as st

from smart_mart.app import create_app
from smart_mart.extensions import db as _db
from smart_mart.models.product import Product
from smart_mart.models.stock_movement import StockMovement
from smart_mart.models.supplier import Supplier
from smart_mart.models.user import User
from smart_mart.services import inventory_manager


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


def _make_supplier(name="Test Supplier"):
    s = Supplier(name=name)
    _db.session.add(s)
    _db.session.flush()
    return s


def _make_user(username="tester"):
    u = User(username=username, password_hash="hash", role="admin")
    _db.session.add(u)
    _db.session.flush()
    return u


def _make_product(sku, quantity=10, supplier_id=None):
    if supplier_id is None:
        supplier_id = _make_supplier(f"Sup-{sku}").id
    p = Product(
        name=f"Product {sku}",
        category="Test",
        sku=sku,
        cost_price=Decimal("50.00"),
        selling_price=Decimal("80.00"),
        quantity=quantity,
        supplier_id=supplier_id,
    )
    _db.session.add(p)
    _db.session.commit()
    return p


# ── Property 5: Unique constraint violations are rejected ────────────────────

def test_duplicate_sku_rejected(app):
    # Feature: smart-mart-inventory, Property 5: Unique constraint violations are rejected
    with app.app_context():
        _make_product("UNIQUE-SKU-001")
        with pytest.raises((ValueError, Exception)):
            _make_product("UNIQUE-SKU-001")


# ── Property 6: Stock adjustment arithmetic is correct ───────────────────────

@settings(max_examples=100)
@given(initial=st.integers(min_value=0, max_value=500), adj=st.integers(min_value=1, max_value=200))
def test_stock_in_arithmetic(app, initial, adj):
    # Feature: smart-mart-inventory, Property 6: Stock adjustment arithmetic is correct
    with app.app_context():
        import uuid
        sku = f"ADJ-IN-{uuid.uuid4().hex[:8]}"
        product = _make_product(sku, quantity=initial)
        user = _make_user(f"u-{uuid.uuid4().hex[:6]}")
        inventory_manager.adjust_stock(product.id, adj, "in", "test", user.id)
        refreshed = _db.session.get(Product, product.id)
        assert refreshed.quantity == initial + adj


@settings(max_examples=100)
@given(initial=st.integers(min_value=1, max_value=500), adj=st.integers(min_value=1, max_value=500))
def test_stock_out_arithmetic(app, initial, adj):
    # Feature: smart-mart-inventory, Property 6: Stock adjustment arithmetic is correct
    with app.app_context():
        import uuid
        if adj > initial:
            return  # skip invalid case (covered by Property 7)
        sku = f"ADJ-OUT-{uuid.uuid4().hex[:8]}"
        product = _make_product(sku, quantity=initial)
        user = _make_user(f"u-{uuid.uuid4().hex[:6]}")
        inventory_manager.adjust_stock(product.id, adj, "out", "test", user.id)
        refreshed = _db.session.get(Product, product.id)
        assert refreshed.quantity == initial - adj


# ── Property 7: Stock-out below zero is rejected ─────────────────────────────

@settings(max_examples=100)
@given(initial=st.integers(min_value=0, max_value=100), excess=st.integers(min_value=1, max_value=50))
def test_stock_out_below_zero_rejected(app, initial, excess):
    # Feature: smart-mart-inventory, Property 7: Stock-out below zero is rejected
    with app.app_context():
        import uuid
        sku = f"BELOW-{uuid.uuid4().hex[:8]}"
        product = _make_product(sku, quantity=initial)
        user = _make_user(f"u-{uuid.uuid4().hex[:6]}")
        amount = initial + excess  # always exceeds stock
        with pytest.raises((ValueError, Exception)):
            inventory_manager.adjust_stock(product.id, amount, "out", "test", user.id)
        refreshed = _db.session.get(Product, product.id)
        assert refreshed.quantity == initial  # unchanged


# ── Property 8: Every stock-changing operation creates a movement record ──────

def test_stock_in_creates_movement(app):
    # Feature: smart-mart-inventory, Property 8: Every stock-changing operation creates a movement record
    with app.app_context():
        import uuid
        sku = f"MOV-IN-{uuid.uuid4().hex[:8]}"
        product = _make_product(sku, quantity=5)
        user = _make_user(f"u-{uuid.uuid4().hex[:6]}")
        before = _db.session.execute(
            _db.select(_db.func.count(StockMovement.id))
            .where(StockMovement.product_id == product.id)
        ).scalar()
        inventory_manager.adjust_stock(product.id, 3, "in", "test", user.id)
        after = _db.session.execute(
            _db.select(_db.func.count(StockMovement.id))
            .where(StockMovement.product_id == product.id)
        ).scalar()
        assert after == before + 1


def test_stock_out_creates_movement(app):
    # Feature: smart-mart-inventory, Property 8: Every stock-changing operation creates a movement record
    with app.app_context():
        import uuid
        sku = f"MOV-OUT-{uuid.uuid4().hex[:8]}"
        product = _make_product(sku, quantity=10)
        user = _make_user(f"u-{uuid.uuid4().hex[:6]}")
        before = _db.session.execute(
            _db.select(_db.func.count(StockMovement.id))
            .where(StockMovement.product_id == product.id)
        ).scalar()
        inventory_manager.adjust_stock(product.id, 2, "out", "test", user.id)
        after = _db.session.execute(
            _db.select(_db.func.count(StockMovement.id))
            .where(StockMovement.product_id == product.id)
        ).scalar()
        assert after == before + 1


# ── Property 25: Product search returns only matching results ─────────────────

@settings(max_examples=50)
@given(query=st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("Ll", "Lu"))))
def test_product_search_name_match(app, query):
    # Feature: smart-mart-inventory, Property 25: Product search returns only matching results
    with app.app_context():
        pagination = inventory_manager.get_products(search=query, page=1)
        items = pagination.items if hasattr(pagination, "items") else list(pagination)
        for product in items:
            assert query.lower() in product.name.lower() or query.lower() in (product.category or "").lower() or query.lower() in (product.sku or "").lower()

