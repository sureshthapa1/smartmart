"""Smoke tests for the Customer Retention & Offer System routes.

Covers:
  GET  /offers/
  GET  /offers/create
  POST /offers/create
  GET  /offers/<id>/edit
  POST /offers/<id>/toggle
  GET  /offers/analytics
  GET  /offers/retention
  GET  /offers/customer/<id>
  GET  /offers/api/customer-offers
  POST /offers/api/assign
  POST /offers/api/apply
  POST /offers/api/rollback
  GET  /offers/api/all-active
  POST /offers/api/quick-create
  GET  /offers/api/ai-suggest
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from smart_mart.extensions import db
from smart_mart.models.customer import Customer
from smart_mart.models.offer import CustomerOffer, Offer
from smart_mart.models.product import Product
from smart_mart.models.user import User
from smart_mart.models.user_permissions import UserPermissions


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def admin_user(db):
    u = User(username="offer_admin", password_hash="x", role="admin")
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def authed_client(client, admin_user):
    """Test client logged in as admin."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_user.id)
        sess["_fresh"] = True
    return client


@pytest.fixture
def sample_offer(db, admin_user):
    offer = Offer(
        title="Test 10% Off",
        offer_type="percentage",
        discount_value=10,
        valid_days=30,
        usage_limit=1,
        status="active",
        created_by=admin_user.id,
    )
    db.session.add(offer)
    db.session.commit()
    return offer


@pytest.fixture
def sample_customer(db):
    c = Customer(name="Test Customer", phone="9800000001", address="Kathmandu")
    db.session.add(c)
    db.session.commit()
    return c


@pytest.fixture
def sample_customer_offer(db, sample_customer, sample_offer):
    co = CustomerOffer(
        customer_id=sample_customer.id,
        offer_id=sample_offer.id,
        assigned_date=date.today(),
        expiry_date=date.today() + timedelta(days=30),
        status=CustomerOffer.STATUS_UNUSED,
    )
    db.session.add(co)
    db.session.commit()
    return co


# ── Page smoke tests ──────────────────────────────────────────────────────────

def test_offer_list_page(authed_client):
    resp = authed_client.get("/offers/")
    assert resp.status_code == 200
    assert b"Offer Management" in resp.data or b"offers" in resp.data.lower()


def test_offer_create_page(authed_client):
    resp = authed_client.get("/offers/create")
    assert resp.status_code == 200
    assert b"Create" in resp.data or b"offer" in resp.data.lower()


def test_offer_analytics_page(authed_client):
    resp = authed_client.get("/offers/analytics")
    assert resp.status_code == 200
    assert b"Analytics" in resp.data or b"conversion" in resp.data.lower()


def test_offer_retention_dashboard(authed_client):
    resp = authed_client.get("/offers/retention")
    assert resp.status_code == 200
    assert b"Retention" in resp.data or b"inactive" in resp.data.lower()


def test_offer_edit_page(authed_client, sample_offer):
    resp = authed_client.get(f"/offers/{sample_offer.id}/edit")
    assert resp.status_code == 200


def test_offer_customer_page(authed_client, sample_customer):
    resp = authed_client.get(f"/offers/customer/{sample_customer.id}")
    assert resp.status_code == 200


# ── POST: create offer ────────────────────────────────────────────────────────

def test_create_offer_post(authed_client, admin_user):
    resp = authed_client.post("/offers/create", data={
        "title": "Smoke Test Offer",
        "offer_type": "percentage",
        "discount_value": "15",
        "valid_days": "14",
        "usage_limit": "1",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Smoke Test Offer" in resp.data or b"created" in resp.data.lower()


def test_create_offer_invalid_percentage(authed_client):
    """Percentage > 100 should be rejected."""
    resp = authed_client.post("/offers/create", data={
        "title": "Bad Offer",
        "offer_type": "percentage",
        "discount_value": "150",
        "valid_days": "7",
        "usage_limit": "1",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"100" in resp.data or b"exceed" in resp.data.lower() or b"danger" in resp.data.lower()


# ── POST: toggle offer ────────────────────────────────────────────────────────

def test_toggle_offer(authed_client, sample_offer):
    original_status = sample_offer.status
    resp = authed_client.post(f"/offers/{sample_offer.id}/toggle", follow_redirects=True)
    assert resp.status_code == 200
    from smart_mart.extensions import db as _db
    refreshed = _db.session.get(Offer, sample_offer.id)
    assert refreshed.status != original_status


# ── API: customer-offers ──────────────────────────────────────────────────────

def test_api_customer_offers_empty(authed_client, sample_customer):
    resp = authed_client.get(f"/offers/api/customer-offers?customer_id={sample_customer.id}")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "offers" in data
    assert isinstance(data["offers"], list)


def test_api_customer_offers_with_offer(authed_client, sample_customer, sample_customer_offer):
    resp = authed_client.get(f"/offers/api/customer-offers?customer_id={sample_customer.id}")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert len(data["offers"]) == 1
    offer_data = data["offers"][0]
    assert offer_data["customer_offer_id"] == sample_customer_offer.id
    assert offer_data["scope"] == "bill"
    assert "title" in offer_data
    assert "expiry_date" in offer_data


def test_api_customer_offers_no_customer_id(authed_client):
    resp = authed_client.get("/offers/api/customer-offers")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["offers"] == []


# ── API: all-active ───────────────────────────────────────────────────────────

def test_api_all_active(authed_client, sample_offer):
    resp = authed_client.get("/offers/api/all-active")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "offers" in data
    ids = [o["id"] for o in data["offers"]]
    assert sample_offer.id in ids


# ── API: assign ───────────────────────────────────────────────────────────────

def test_api_assign_offer(authed_client, sample_customer, sample_offer):
    resp = authed_client.post(
        "/offers/api/assign",
        data=json.dumps({"customer_id": sample_customer.id, "offer_id": sample_offer.id}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["success"] is True
    assert "customer_offer_id" in data


def test_api_assign_offer_duplicate(authed_client, sample_customer, sample_offer, sample_customer_offer):
    """Assigning same offer twice returns is_duplicate=True."""
    resp = authed_client.post(
        "/offers/api/assign",
        data=json.dumps({"customer_id": sample_customer.id, "offer_id": sample_offer.id}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["success"] is True
    assert data["is_duplicate"] is True


def test_api_assign_offer_missing_params(authed_client):
    resp = authed_client.post(
        "/offers/api/assign",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_api_assign_offer_inactive(authed_client, sample_customer, db, admin_user):
    """Assigning an inactive offer should fail."""
    inactive = Offer(
        title="Inactive Offer",
        offer_type="fixed",
        discount_value=50,
        valid_days=7,
        usage_limit=1,
        status="inactive",
        created_by=admin_user.id,
    )
    db.session.add(inactive)
    db.session.commit()
    resp = authed_client.post(
        "/offers/api/assign",
        data=json.dumps({"customer_id": sample_customer.id, "offer_id": inactive.id}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert data["success"] is False


# ── API: apply ────────────────────────────────────────────────────────────────

def test_api_apply_offer(authed_client, sample_customer_offer):
    resp = authed_client.post(
        "/offers/api/apply",
        data=json.dumps({
            "customer_offer_id": sample_customer_offer.id,
            "sale_id": 0,
            "cart_total": 500.0,
            "customer_id": sample_customer_offer.customer_id,
        }),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["success"] is True
    assert data["discount_amount"] == 50.0  # 10% of 500
    assert data["scope"] == "bill"


def test_api_apply_offer_wrong_customer(authed_client, sample_customer_offer):
    """Applying another customer's offer should fail."""
    resp = authed_client.post(
        "/offers/api/apply",
        data=json.dumps({
            "customer_offer_id": sample_customer_offer.id,
            "sale_id": 0,
            "cart_total": 500.0,
            "customer_id": 99999,  # wrong customer
        }),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert data["success"] is False


def test_api_apply_offer_below_min_purchase(authed_client, db, sample_customer, admin_user):
    """Offer with min_purchase should reject carts below threshold."""
    offer = Offer(
        title="Min Purchase Offer",
        offer_type="percentage",
        discount_value=20,
        min_purchase_amount=1000,
        valid_days=30,
        usage_limit=1,
        status="active",
        created_by=admin_user.id,
    )
    db.session.add(offer)
    db.session.flush()
    co = CustomerOffer(
        customer_id=sample_customer.id,
        offer_id=offer.id,
        assigned_date=date.today(),
        expiry_date=date.today() + timedelta(days=30),
        status=CustomerOffer.STATUS_UNUSED,
    )
    db.session.add(co)
    db.session.commit()

    resp = authed_client.post(
        "/offers/api/apply",
        data=json.dumps({
            "customer_offer_id": co.id,
            "sale_id": 0,
            "cart_total": 200.0,  # below 1000 minimum
            "customer_id": sample_customer.id,
        }),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert data["success"] is False
    assert "minimum" in data["error"].lower()


def test_api_apply_offer_already_used(authed_client, db, sample_customer_offer):
    """Applying an already-used offer should fail."""
    sample_customer_offer.status = CustomerOffer.STATUS_USED
    db.session.commit()

    resp = authed_client.post(
        "/offers/api/apply",
        data=json.dumps({
            "customer_offer_id": sample_customer_offer.id,
            "sale_id": 0,
            "cart_total": 500.0,
        }),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert data["success"] is False


# ── API: rollback ─────────────────────────────────────────────────────────────

def test_api_rollback_offer(authed_client, db, sample_customer_offer):
    """Rollback reverts a used offer back to unused."""
    sample_customer_offer.status = CustomerOffer.STATUS_USED
    sample_customer_offer.applied_sale_id = 42
    sample_customer_offer.usage_count = 1
    db.session.commit()

    resp = authed_client.post(
        "/offers/api/rollback",
        data=json.dumps({"sale_id": 42}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["success"] is True
    assert data["reverted"] == 1

    from smart_mart.extensions import db as _db
    refreshed = _db.session.get(CustomerOffer, sample_customer_offer.id)
    assert refreshed.status == CustomerOffer.STATUS_UNUSED
    assert refreshed.applied_sale_id is None
    assert refreshed.usage_count == 0


def test_api_rollback_no_offer(authed_client):
    """Rollback on a sale with no offer returns reverted=0."""
    resp = authed_client.post(
        "/offers/api/rollback",
        data=json.dumps({"sale_id": 99999}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["success"] is True
    assert data["reverted"] == 0


# ── API: quick-create ─────────────────────────────────────────────────────────

def test_api_quick_create(authed_client, sample_customer):
    resp = authed_client.post(
        "/offers/api/quick-create",
        data=json.dumps({
            "customer_id": sample_customer.id,
            "title": "Quick Offer",
            "offer_type": "fixed",
            "discount_value": 50,
            "valid_days": 7,
        }),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["success"] is True
    assert "offer_id" in data
    assert "customer_offer_id" in data


def test_api_quick_create_invalid_percentage(authed_client, sample_customer):
    """Percentage > 100 should be rejected."""
    resp = authed_client.post(
        "/offers/api/quick-create",
        data=json.dumps({
            "customer_id": sample_customer.id,
            "title": "Bad Quick Offer",
            "offer_type": "percentage",
            "discount_value": 200,
            "valid_days": 7,
        }),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert data["success"] is False


# ── API: ai-suggest ───────────────────────────────────────────────────────────

def test_api_ai_suggest(authed_client, sample_customer):
    resp = authed_client.get(f"/offers/api/ai-suggest?customer_id={sample_customer.id}")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "suggestions" in data
    assert isinstance(data["suggestions"], list)


def test_api_ai_suggest_no_customer(authed_client):
    resp = authed_client.get("/offers/api/ai-suggest")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["suggestions"] == []


# ── Product-based offer scope ─────────────────────────────────────────────────

def test_product_based_offer_scope(authed_client, db, sample_customer, admin_user):
    """product_based offers should have scope='product' in the API response."""
    product = Product(
        name="Cashews Jumbo",
        sku="CASHEW-J",
        category="Snacks",
        cost_price=200,
        selling_price=300,
        quantity=10,
    )
    db.session.add(product)
    db.session.flush()

    offer = Offer(
        title="5% off Cashews",
        offer_type="product_based",
        discount_value=5,
        product_id=product.id,
        valid_days=14,
        usage_limit=1,
        status="active",
        created_by=admin_user.id,
    )
    db.session.add(offer)
    db.session.flush()

    co = CustomerOffer(
        customer_id=sample_customer.id,
        offer_id=offer.id,
        assigned_date=date.today(),
        expiry_date=date.today() + timedelta(days=14),
        status=CustomerOffer.STATUS_UNUSED,
    )
    db.session.add(co)
    db.session.commit()

    resp = authed_client.get(f"/offers/api/customer-offers?customer_id={sample_customer.id}")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    row = next(o for o in data["offers"] if o["customer_offer_id"] == co.id)
    assert row["scope"] == "product"
    assert row["product_id"] == product.id
    assert row["product_name"] == "Cashews Jumbo"
