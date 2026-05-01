"""Property-based tests for alert engine.

Property 24: Alert thresholds correctly classify products
"""
# Feature: smart-mart-inventory

from datetime import date, timedelta
from decimal import Decimal

import pytest
@pytest.mark.slow`nfrom hypothesis import given, settings
from hypothesis import strategies as st

from smart_mart.app import create_app
from smart_mart.extensions import db as _db
from smart_mart.models.product import Product
from smart_mart.models.supplier import Supplier
from smart_mart.services.alert_engine import get_low_stock_alerts, get_expiry_alerts


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


def _make_product(sku, quantity, expiry_date=None):
    import uuid
    supplier = Supplier(name=f"Sup-{uuid.uuid4().hex[:6]}")
    _db.session.add(supplier)
    _db.session.flush()
    product = Product(
        name=f"P-{sku}",
        category="Test",
        sku=sku,
        cost_price=Decimal("10.00"),
        selling_price=Decimal("20.00"),
        quantity=quantity,
        supplier_id=supplier.id,
        expiry_date=expiry_date,
    )
    _db.session.add(product)
    _db.session.commit()
    return product


# ── Property 24: Alert thresholds correctly classify products ─────────────────

@settings(max_examples=50, deadline=None)
@given(qty=st.integers(min_value=0, max_value=9))
def test_low_stock_alert_includes_below_threshold(app, qty):
    # Feature: smart-mart-inventory, Property 24: Alert thresholds correctly classify products
    with app.app_context():
        import uuid
        threshold = 10
        product = _make_product(f"LOW-{uuid.uuid4().hex[:8]}", quantity=qty)
        alerts = get_low_stock_alerts(threshold=threshold)
        alert_ids = {p.id for p in alerts}
        assert product.id in alert_ids, f"qty={qty} should trigger low stock alert"


@settings(max_examples=50, deadline=None)
@given(qty=st.integers(min_value=11, max_value=100))
def test_low_stock_alert_excludes_above_threshold(app, qty):
    # Feature: smart-mart-inventory, Property 24: Alert thresholds correctly classify products
    with app.app_context():
        import uuid
        threshold = 10
        product = _make_product(f"OK-{uuid.uuid4().hex[:8]}", quantity=qty)
        alerts = get_low_stock_alerts(threshold=threshold)
        alert_ids = {p.id for p in alerts}
        assert product.id not in alert_ids, f"qty={qty} should NOT trigger low stock alert"


def test_expiry_alert_includes_soon_expiring(app):
    # Feature: smart-mart-inventory, Property 24: Alert thresholds correctly classify products
    with app.app_context():
        import uuid
        soon = date.today() + timedelta(days=15)
        product = _make_product(f"EXP-SOON-{uuid.uuid4().hex[:8]}", quantity=5, expiry_date=soon)
        alerts = get_expiry_alerts(days=30)
        alert_ids = {p.id for p in alerts}
        assert product.id in alert_ids


def test_expiry_alert_excludes_far_future(app):
    # Feature: smart-mart-inventory, Property 24: Alert thresholds correctly classify products
    with app.app_context():
        import uuid
        far = date.today() + timedelta(days=365)
        product = _make_product(f"EXP-FAR-{uuid.uuid4().hex[:8]}", quantity=5, expiry_date=far)
        alerts = get_expiry_alerts(days=30)
        alert_ids = {p.id for p in alerts}
        assert product.id not in alert_ids

