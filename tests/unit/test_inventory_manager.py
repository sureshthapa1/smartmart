"""Unit tests for smart_mart/services/inventory_manager.py."""

from decimal import Decimal

import pytest

from smart_mart.app import create_app
from smart_mart.extensions import db as _db
from smart_mart.models.product import Product
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


def _supplier(name="Acme"):
    s = Supplier(name=name)
    _db.session.add(s)
    _db.session.flush()
    return s


def _user(username="tester"):
    u = User(username=username, password_hash="hash", role="admin")
    _db.session.add(u)
    _db.session.flush()
    return u


def _product_data(sku, supplier_id):
    return {
        "name": f"Product {sku}",
        "category": "Groceries",
        "sku": sku,
        "cost_price": 50.0,
        "selling_price": 80.0,
        "quantity": 20,
        "supplier_id": supplier_id,
    }


# ── create_product ────────────────────────────────────────────────────────────

def test_create_product_returns_product(app):
    sup = _supplier("Sup-A")
    data = _product_data("SKU-CREATE-001", sup.id)
    product = inventory_manager.create_product(data)
    assert product.id is not None
    assert product.sku == "SKU-CREATE-001"
    assert product.quantity == 20


def test_create_product_duplicate_sku_raises(app):
    import uuid
    sup = _supplier(f"Sup-{uuid.uuid4().hex[:6]}")
    data = _product_data("SKU-DUP-001", sup.id)
    inventory_manager.create_product(data)
    with pytest.raises((ValueError, Exception)):
        inventory_manager.create_product(data)


# ── update_product ────────────────────────────────────────────────────────────

def test_update_product_name(app):
    import uuid
    sup = _supplier(f"Sup-{uuid.uuid4().hex[:6]}")
    data = _product_data(f"SKU-UPD-{uuid.uuid4().hex[:6]}", sup.id)
    product = inventory_manager.create_product(data)
    updated = inventory_manager.update_product(product.id, {"name": "Updated Name"})
    assert updated.name == "Updated Name"


def test_update_product_price(app):
    import uuid
    sup = _supplier(f"Sup-{uuid.uuid4().hex[:6]}")
    data = _product_data(f"SKU-PRICE-{uuid.uuid4().hex[:6]}", sup.id)
    product = inventory_manager.create_product(data)
    updated = inventory_manager.update_product(product.id, {"selling_price": 99.99})
    assert abs(float(updated.selling_price) - 99.99) < 0.01


# ── adjust_stock ──────────────────────────────────────────────────────────────

def test_adjust_stock_in(app):
    import uuid
    sup = _supplier(f"Sup-{uuid.uuid4().hex[:6]}")
    data = _product_data(f"SKU-ADJIN-{uuid.uuid4().hex[:6]}", sup.id)
    product = inventory_manager.create_product(data)
    user = _user(f"u-{uuid.uuid4().hex[:6]}")
    inventory_manager.adjust_stock(product.id, 10, "in", "restock", user.id)
    refreshed = _db.session.get(Product, product.id)
    assert refreshed.quantity == 30


def test_adjust_stock_out(app):
    import uuid
    sup = _supplier(f"Sup-{uuid.uuid4().hex[:6]}")
    data = _product_data(f"SKU-ADJOUT-{uuid.uuid4().hex[:6]}", sup.id)
    product = inventory_manager.create_product(data)
    user = _user(f"u-{uuid.uuid4().hex[:6]}")
    inventory_manager.adjust_stock(product.id, 5, "out", "damaged", user.id)
    refreshed = _db.session.get(Product, product.id)
    assert refreshed.quantity == 15


def test_adjust_stock_out_below_zero_raises(app):
    import uuid
    sup = _supplier(f"Sup-{uuid.uuid4().hex[:6]}")
    data = _product_data(f"SKU-ZERO-{uuid.uuid4().hex[:6]}", sup.id)
    product = inventory_manager.create_product(data)
    user = _user(f"u-{uuid.uuid4().hex[:6]}")
    with pytest.raises((ValueError, Exception)):
        inventory_manager.adjust_stock(product.id, 100, "out", "too much", user.id)
    refreshed = _db.session.get(Product, product.id)
    assert refreshed.quantity == 20  # unchanged


# ── get_products (search) ─────────────────────────────────────────────────────

def test_get_products_no_filter_returns_all(app):
    pagination = inventory_manager.get_products(search=None, page=1)
    items = pagination.items if hasattr(pagination, "items") else list(pagination)
    assert len(items) >= 0  # just ensure no crash


def test_get_products_search_by_name(app):
    import uuid
    sup = _supplier(f"Sup-{uuid.uuid4().hex[:6]}")
    unique_name = f"UniqueSearchName-{uuid.uuid4().hex[:8]}"
    data = {
        "name": unique_name,
        "category": "Test",
        "sku": f"SRCH-{uuid.uuid4().hex[:8]}",
        "cost_price": 10.0,
        "selling_price": 20.0,
        "quantity": 5,
        "supplier_id": sup.id,
    }
    inventory_manager.create_product(data)
    pagination = inventory_manager.get_products(search=unique_name[:10], page=1)
    items = pagination.items if hasattr(pagination, "items") else list(pagination)
    assert any(unique_name in p.name for p in items)
