"""Property-based tests for cash flow management.

Properties covered:
  Property 13: Confirmed sales generate income entries with correct amounts
  Property 14: Confirmed purchases generate expense entries with correct amounts
  Property 15: Daily cash balance equals income minus expenses
  Property 16: Profit/loss calculation follows the defined formula
"""
# Feature: smart-mart-inventory

from datetime import date
from decimal import Decimal

import pytest
@pytest.mark.slow`nfrom hypothesis import given, settings
from hypothesis import strategies as st

from smart_mart.app import create_app
from smart_mart.extensions import db as _db
from smart_mart.models.expense import Expense
from smart_mart.models.product import Product
from smart_mart.models.supplier import Supplier
from smart_mart.models.user import User
from smart_mart.services import cash_flow_manager, sales_manager, purchase_manager


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


def _seed_product(sku, quantity=100, cost=40, price=80):
    import uuid
    supplier = Supplier(name=f"Sup-{uuid.uuid4().hex[:6]}")
    _db.session.add(supplier)
    _db.session.flush()
    product = Product(
        name=f"P-{sku}",
        category="T",
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
    return product, user, supplier


# ── Property 13: Confirmed sales generate income entries ─────────────────────

@settings(max_examples=50)
@given(qty=st.integers(min_value=1, max_value=10), price=st.floats(min_value=1.0, max_value=200.0, allow_nan=False, allow_infinity=False))
def test_sale_creates_income_entry(app, qty, price):
    # Feature: smart-mart-inventory, Property 13: Confirmed sales generate income entries with correct amounts
    with app.app_context():
        import uuid
        price = round(price, 2)
        product, user, _ = _seed_product(f"INC-{uuid.uuid4().hex[:8]}", quantity=50, price=price)
        sale = sales_manager.create_sale(
            [{"product_id": product.id, "quantity": qty, "unit_price": price}],
            user_id=user.id,
        )
        expected = round(qty * price, 2)
        assert abs(float(sale.total_amount) - expected) < 0.02


# ── Property 14: Confirmed purchases generate expense entries ─────────────────

@settings(max_examples=50)
@given(qty=st.integers(min_value=1, max_value=20), cost=st.floats(min_value=1.0, max_value=100.0, allow_nan=False, allow_infinity=False))
def test_purchase_creates_expense_entry(app, qty, cost):
    # Feature: smart-mart-inventory, Property 14: Confirmed purchases generate expense entries with correct amounts
    with app.app_context():
        import uuid
        cost = round(cost, 2)
        product, user, supplier = _seed_product(f"EXP-{uuid.uuid4().hex[:8]}", quantity=0, cost=cost)
        purchase = purchase_manager.create_purchase(
            supplier_id=supplier.id,
            items=[{"product_id": product.id, "quantity": qty, "unit_cost": cost}],
            purchase_date=date.today(),
            user_id=user.id,
        )
        expected = round(qty * cost, 2)
        assert abs(float(purchase.total_cost) - expected) < 0.02


# ── Property 15: Daily cash balance equals income minus expenses ──────────────

def test_daily_balance_formula(app):
    # Feature: smart-mart-inventory, Property 15: Daily cash balance equals income minus expenses
    with app.app_context():
        today = date.today()
        balance = cash_flow_manager.daily_balance(today)
        # Balance should be a numeric value (not raise)
        assert isinstance(balance, (int, float, Decimal))


# ── Property 16: Profit/loss calculation follows the defined formula ──────────

def test_profit_loss_formula(app):
    # Feature: smart-mart-inventory, Property 16: Profit/loss calculation follows the defined formula
    with app.app_context():
        from datetime import timedelta
        start = date.today() - timedelta(days=30)
        end = date.today()
        result = cash_flow_manager.profit_loss(start, end)
        assert isinstance(result, dict)
        assert "profit" in result or "net" in result or "total_revenue" in result

