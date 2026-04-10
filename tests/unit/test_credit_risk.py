"""Unit tests for Customer Credit Risk Score feature.

Covers:
  - Risk score computation (Requirement 1)
  - Risk score persistence (Requirement 2)
  - Admin override (Requirement 6)
  - Recalculate all (Requirement 7)
  - API endpoint (Requirement 5)
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from smart_mart.app import create_app
from smart_mart.extensions import db as _db
from smart_mart.models.customer_risk_score import CustomerRiskScore
from smart_mart.models.product import Product
from smart_mart.models.supplier import Supplier
from smart_mart.models.user import User
from smart_mart.services.credit_risk_service import (
    calculate_risk_score,
    get_risk_for_customer,
    recalculate_all,
    set_override,
    _score_to_tier,
)


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


def _seed_credit_sale(customer_name, amount=500.0, collected=False, due_date=None):
    import uuid
    from smart_mart.models.sale import Sale, SaleItem
    supplier = Supplier(name=f"Sup-{uuid.uuid4().hex[:4]}")
    _db.session.add(supplier)
    _db.session.flush()
    product = Product(
        name=f"P-{uuid.uuid4().hex[:4]}",
        category="T",
        sku=f"SKU-{uuid.uuid4().hex[:8]}",
        cost_price=Decimal("10.00"),
        selling_price=Decimal("20.00"),
        quantity=100,
        supplier_id=supplier.id,
    )
    _db.session.add(product)
    user = User(username=f"u-{uuid.uuid4().hex[:6]}", password_hash="h", role="admin")
    _db.session.add(user)
    _db.session.flush()

    sale = Sale(
        user_id=user.id,
        total_amount=Decimal(str(amount)),
        payment_mode="credit",
        customer_name=customer_name,
        credit_collected=collected,
        credit_due_date=due_date,
    )
    _db.session.add(sale)
    _db.session.flush()
    item = SaleItem(
        sale_id=sale.id,
        product_id=product.id,
        quantity=1,
        unit_price=Decimal(str(amount)),
        subtotal=Decimal(str(amount)),
    )
    _db.session.add(item)
    _db.session.commit()
    return sale, user


# ── Requirement 1: Risk Score Computation ────────────────────────────────────

def test_no_credit_history_returns_safe(app):
    data = calculate_risk_score("NoHistoryCustomer999")
    assert data["score"] == 0
    assert data["risk_tier"] == "safe"
    assert data["total_credit_sales"] == 0


def test_score_in_range_0_to_100(app):
    import uuid
    name = f"Customer-{uuid.uuid4().hex[:8]}"
    _seed_credit_sale(name, amount=1000.0, collected=False,
                      due_date=date.today() - timedelta(days=10))
    data = calculate_risk_score(name)
    assert 0 <= data["score"] <= 100


def test_overdue_customer_has_higher_risk(app):
    import uuid
    name_good = f"GoodPayer-{uuid.uuid4().hex[:6]}"
    name_bad = f"BadPayer-{uuid.uuid4().hex[:6]}"
    # Good payer: no credit history at all → score 0 (safe)
    # Bad payer: overdue credit → higher score
    _seed_credit_sale(name_bad, amount=500.0, collected=False,
                      due_date=date.today() - timedelta(days=30))
    good = calculate_risk_score(name_good)   # no history → score 0
    bad = calculate_risk_score(name_bad)
    assert bad["score"] >= good["score"]
    assert good["risk_tier"] == "safe"


def test_tier_mapping():
    assert _score_to_tier(0) == "safe"
    assert _score_to_tier(39) == "safe"
    assert _score_to_tier(40) == "moderate"
    assert _score_to_tier(69) == "moderate"
    assert _score_to_tier(70) == "risky"
    assert _score_to_tier(100) == "risky"


# ── Requirement 2: Risk Score Persistence ────────────────────────────────────

def test_score_is_persisted(app):
    import uuid
    name = f"PersistTest-{uuid.uuid4().hex[:6]}"
    _seed_credit_sale(name, amount=300.0)
    calculate_risk_score(name)
    row = _db.session.execute(
        _db.select(CustomerRiskScore).where(CustomerRiskScore.customer_name == name)
    ).scalar_one_or_none()
    assert row is not None
    assert row.last_computed_at is not None


def test_stored_score_returned_by_get_risk_for_customer(app):
    import uuid
    name = f"StoredTest-{uuid.uuid4().hex[:6]}"
    _seed_credit_sale(name, amount=200.0)
    computed = calculate_risk_score(name)
    stored = get_risk_for_customer(name)
    assert stored["score"] == computed["score"]
    assert stored["risk_tier"] == computed["risk_tier"]


# ── Requirement 6: Admin Override ────────────────────────────────────────────

def test_set_override_changes_effective_tier(app):
    import uuid
    name = f"OverrideTest-{uuid.uuid4().hex[:6]}"
    _seed_credit_sale(name, amount=100.0)
    calculate_risk_score(name)
    admin = User(username=f"admin-{uuid.uuid4().hex[:6]}", password_hash="h", role="admin")
    _db.session.add(admin)
    _db.session.commit()

    set_override(name, "risky", admin.id)
    row = _db.session.execute(
        _db.select(CustomerRiskScore).where(CustomerRiskScore.customer_name == name)
    ).scalar_one()
    assert row.override_tier == "risky"
    assert row.effective_tier == "risky"
    assert row.has_override is True


def test_clear_override_reverts_to_computed(app):
    import uuid
    name = f"ClearOverride-{uuid.uuid4().hex[:6]}"
    _seed_credit_sale(name, amount=100.0)
    calculate_risk_score(name)
    admin = User(username=f"admin-{uuid.uuid4().hex[:6]}", password_hash="h", role="admin")
    _db.session.add(admin)
    _db.session.commit()

    set_override(name, "risky", admin.id)
    set_override(name, None, admin.id)  # clear
    row = _db.session.execute(
        _db.select(CustomerRiskScore).where(CustomerRiskScore.customer_name == name)
    ).scalar_one()
    assert row.override_tier is None
    assert row.has_override is False
    assert row.effective_tier == row.risk_tier


def test_invalid_override_tier_raises(app):
    import uuid
    name = f"InvalidOverride-{uuid.uuid4().hex[:6]}"
    _seed_credit_sale(name, amount=100.0)
    calculate_risk_score(name)
    admin = User(username=f"admin-{uuid.uuid4().hex[:6]}", password_hash="h", role="admin")
    _db.session.add(admin)
    _db.session.commit()
    with pytest.raises(ValueError):
        set_override(name, "unknown_tier", admin.id)


# ── Requirement 7: Recalculate All ───────────────────────────────────────────

def test_recalculate_all_updates_scores(app):
    import uuid
    name = f"RecalcTest-{uuid.uuid4().hex[:6]}"
    _seed_credit_sale(name, amount=400.0)
    count = recalculate_all()
    assert count >= 1


def test_recalculate_all_preserves_overrides(app):
    import uuid
    name = f"PreserveOverride-{uuid.uuid4().hex[:6]}"
    _seed_credit_sale(name, amount=300.0)
    calculate_risk_score(name)
    admin = User(username=f"admin-{uuid.uuid4().hex[:6]}", password_hash="h", role="admin")
    _db.session.add(admin)
    _db.session.commit()
    set_override(name, "moderate", admin.id)

    recalculate_all()

    row = _db.session.execute(
        _db.select(CustomerRiskScore).where(CustomerRiskScore.customer_name == name)
    ).scalar_one()
    assert row.override_tier == "moderate"  # preserved


# ── Requirement 5: API endpoint ───────────────────────────────────────────────

def test_api_customer_risk_endpoint(app):
    import uuid
    name = f"APITest-{uuid.uuid4().hex[:6]}"
    _seed_credit_sale(name, amount=500.0)
    calculate_risk_score(name)

    # Create admin user and log in
    admin = User(username=f"api-admin-{uuid.uuid4().hex[:6]}", password_hash="h", role="admin")
    _db.session.add(admin)
    _db.session.commit()

    with app.test_client() as client:
        # Login
        from smart_mart.services.authenticator import hash_password
        admin.password_hash = hash_password("testpass")
        _db.session.commit()
        client.post("/auth/login", data={"username": admin.username, "password": "testpass"})

        response = client.get(f"/api/customer-risk/{name}")
        assert response.status_code == 200
        data = response.get_json()
        assert "risk_tier" in data
        assert "risk_score" in data
        assert "total_outstanding" in data


def test_api_unknown_customer_returns_safe(app):
    import uuid
    admin = User(username=f"api-admin2-{uuid.uuid4().hex[:6]}", password_hash="h", role="admin")
    _db.session.add(admin)
    _db.session.commit()

    with app.test_client() as client:
        from smart_mart.services.authenticator import hash_password
        admin.password_hash = hash_password("testpass")
        _db.session.commit()
        client.post("/auth/login", data={"username": admin.username, "password": "testpass"})

        response = client.get("/api/customer-risk/UnknownCustomerXYZ999")
        assert response.status_code == 200
        data = response.get_json()
        assert data["risk_tier"] == "safe"
        assert data["total_outstanding"] == 0.0
