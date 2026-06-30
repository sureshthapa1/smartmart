"""Tests for the customer storefront checkout/payment flow.

This file specifically locks in three security fixes:

1. eSewa callback verification fails closed when ESEWA_SECRET_KEY is unset
   (previously fell back to eSewa's publicly-documented sandbox secret,
   making payment confirmation forgeable by anyone).
2. The MCP tools endpoint fails closed when MCP_SECRET is unset (previously
   allowed all unauthenticated requests).
3. /store/track carries a rate limit (previously unlimited, allowing
   brute-force enumeration of order numbers / phone numbers, each of which
   returns full customer PII on a hit).

It also covers the basic checkout happy path, since no test previously
exercised the customer-facing checkout/payment routes at all.
"""
import base64
import hashlib
import hmac

from smart_mart.extensions import db
from smart_mart.models.product import Product
from smart_mart.models.online_order import OnlineOrder

# eSewa's publicly-documented sandbox secret key. This used to be the
# hardcoded fallback in _esewa_secret() when ESEWA_SECRET_KEY was unset —
# i.e. anyone who knew this (public) string could forge a valid signature.
ESEWA_PUBLIC_SANDBOX_SECRET = "8gBm/:&EnhH.1/q"


def _product(price=850, quantity=10):
    product = Product(
        name="Premium Cashews",
        category="Dry Fruits",
        sku="CSH-TEST-001",
        cost_price=500,
        selling_price=price,
        quantity=quantity,
        low_stock_threshold=2,
        is_active=True,
    )
    db.session.add(product)
    db.session.commit()
    return product


def _add_to_cart(client, product, qty=1):
    with client.session_transaction() as sess:
        sess["cart"] = {str(product.id): qty}


def _checkout_form(**overrides):
    data = {
        "name": "Sita Sharma",
        "phone": "9800000001",
        "email": "sita@example.com",
        "address": "Lazimpat, Kathmandu",
        "area": "Lazimpat",
        "payment_mode": "cod",
    }
    data.update(overrides)
    return data


def _esewa_sign(secret: str, total_amount: str, transaction_uuid: str, product_code: str) -> str:
    """Independently compute an eSewa v2 signature (mirrors the documented
    algorithm), so tests don't just validate the app against itself."""
    message = f"total_amount={total_amount},transaction_uuid={transaction_uuid},product_code={product_code}"
    sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(sig).decode()


def _get_order(order_number):
    return db.session.execute(
        db.select(OnlineOrder).where(OnlineOrder.order_number == order_number)
    ).scalar_one_or_none()


# ── Checkout happy path ────────────────────────────────────────────────────

def test_checkout_cod_creates_order_and_redirects_to_success(client):
    product = _product(price=850)
    _add_to_cart(client, product, qty=1)

    resp = client.post("/store/checkout", data=_checkout_form(payment_mode="cod"))

    assert resp.status_code == 302
    assert "/success" in resp.headers["Location"]

    order = db.session.execute(db.select(OnlineOrder)).scalars().first()
    assert order is not None
    assert order.customer_phone == "9800000001"
    # Server recalculated the total from the DB price, not anything client-supplied.
    assert float(order.grand_total) == 850.0 + 100.0  # + standard delivery charge


def test_checkout_rejects_invalid_phone_without_creating_order(client):
    product = _product()
    _add_to_cart(client, product, qty=1)

    resp = client.post("/store/checkout", data=_checkout_form(phone="12345"))

    assert resp.status_code == 200  # re-renders checkout with errors, no redirect
    assert db.session.execute(db.select(OnlineOrder)).scalars().first() is None


def test_checkout_esewa_redirects_to_pending_not_success(client):
    product = _product()
    _add_to_cart(client, product, qty=1)

    resp = client.post("/store/checkout", data=_checkout_form(payment_mode="esewa"))

    assert resp.status_code == 302
    assert "/payment" in resp.headers["Location"]

    order = db.session.execute(db.select(OnlineOrder)).scalars().first()
    assert order is not None
    assert order.payment_status != "paid"


# ── eSewa callback: fail-closed regression tests ───────────────────────────

def test_esewa_callback_rejected_when_secret_not_configured(client, monkeypatch):
    """The core regression test for the fix: even if a request is signed
    with eSewa's publicly-known sandbox secret, an unconfigured deployment
    must NOT accept it as proof of payment."""
    monkeypatch.delenv("ESEWA_SECRET_KEY", raising=False)

    product = _product(price=850)
    _add_to_cart(client, product, qty=1)
    client.post("/store/checkout", data=_checkout_form(payment_mode="esewa"))
    order = db.session.execute(db.select(OnlineOrder)).scalars().first()
    order_number = order.order_number

    total_amount = f"{float(order.grand_total):.2f}"
    signature = _esewa_sign(ESEWA_PUBLIC_SANDBOX_SECRET, total_amount, order_number, "EPAYTEST")

    resp = client.get(
        f"/store/payment/{order_number}/callback/esewa",
        query_string={
            "signed_field_names": "total_amount,transaction_uuid,product_code",
            "total_amount": total_amount,
            "transaction_uuid": order_number,
            "product_code": "EPAYTEST",
            "signature": signature,
            "transaction_code": "FORGED123",
        },
    )

    assert resp.status_code == 302
    assert "/payment" in resp.headers["Location"]  # back to pending, not success

    db.session.refresh(order)
    assert order.payment_status != "paid"


def test_esewa_callback_accepted_with_correctly_configured_secret(client, monkeypatch):
    """Positive control: when ESEWA_SECRET_KEY *is* configured and the
    signature is correct, the callback should still work."""
    monkeypatch.setenv("ESEWA_SECRET_KEY", "a-real-merchant-secret-not-public")

    product = _product(price=850)
    _add_to_cart(client, product, qty=1)
    client.post("/store/checkout", data=_checkout_form(payment_mode="esewa"))
    order = db.session.execute(db.select(OnlineOrder)).scalars().first()
    order_number = order.order_number

    total_amount = f"{float(order.grand_total):.2f}"
    signature = _esewa_sign(
        "a-real-merchant-secret-not-public", total_amount, order_number, "EPAYTEST"
    )

    resp = client.get(
        f"/store/payment/{order_number}/callback/esewa",
        query_string={
            "signed_field_names": "total_amount,transaction_uuid,product_code",
            "total_amount": total_amount,
            "transaction_uuid": order_number,
            "product_code": "EPAYTEST",
            "signature": signature,
            "transaction_code": "REAL123",
        },
    )

    assert resp.status_code == 302
    assert "/success" in resp.headers["Location"]

    db.session.refresh(order)
    assert order.payment_status == "paid"


def test_esewa_callback_rejected_with_tampered_signature(client, monkeypatch):
    """Even with a secret configured, a signature that doesn't match the
    payload must be rejected (covers basic HMAC-verification correctness)."""
    monkeypatch.setenv("ESEWA_SECRET_KEY", "a-real-merchant-secret-not-public")

    product = _product(price=850)
    _add_to_cart(client, product, qty=1)
    client.post("/store/checkout", data=_checkout_form(payment_mode="esewa"))
    order = db.session.execute(db.select(OnlineOrder)).scalars().first()
    order_number = order.order_number

    resp = client.get(
        f"/store/payment/{order_number}/callback/esewa",
        query_string={
            "signed_field_names": "total_amount,transaction_uuid,product_code",
            "total_amount": f"{float(order.grand_total):.2f}",
            "transaction_uuid": order_number,
            "product_code": "EPAYTEST",
            "signature": "obviously-not-a-real-signature",
        },
    )

    assert resp.status_code == 302
    assert "/payment" in resp.headers["Location"]
    db.session.refresh(order)
    assert order.payment_status != "paid"


# ── MCP tools: fail-closed regression tests ────────────────────────────────

def test_mcp_tools_rejected_when_secret_not_configured_and_anonymous(client, monkeypatch):
    """The core regression test for the MCP fix: with no MCP_SECRET set and
    no admin session, the tools endpoint must NOT be open to the world."""
    monkeypatch.delenv("MCP_SECRET", raising=False)

    resp = client.get("/mcp/tools")

    assert resp.status_code == 401


def test_mcp_tools_accepted_with_correct_bearer_token(client, monkeypatch):
    monkeypatch.setenv("MCP_SECRET", "a-real-mcp-secret")

    resp = client.get("/mcp/tools", headers={"Authorization": "Bearer a-real-mcp-secret"})

    assert resp.status_code == 200
    assert resp.get_json()["tools"]


def test_mcp_tools_rejected_with_wrong_bearer_token(client, monkeypatch):
    monkeypatch.setenv("MCP_SECRET", "a-real-mcp-secret")

    resp = client.get("/mcp/tools", headers={"Authorization": "Bearer wrong-token"})

    assert resp.status_code == 401


# ── /track rate limiting ───────────────────────────────────────────────────

def test_track_route_is_rate_limited(client):
    """End-to-end: /store/track returns 429 after exceeding the configured
    10/minute limit. This is the route that returns full customer PII
    (name, phone, address) on an order-number lookup with no ownership
    check, so it must stay rate-limited."""
    from smart_mart.extensions import limiter

    limiter.reset()
    for _ in range(10):
        resp = client.get("/store/track")
        assert resp.status_code != 429

    resp = client.get("/store/track")
    assert resp.status_code == 429
