"""Route-level tests for the discount permission guard on the POS.

UserPermissions.can_give_discount is a real column with a toggle in the admin
UI, but nothing enforced it: the discount field ships in the POS form, so any
cashier who could ring up a sale could also discount it to zero. The service
layer cannot decide this — it has no request user — so the check lives in the
route and these tests pin it there.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from smart_mart.extensions import db as _db
from smart_mart.models.product import Product
from smart_mart.models.sale import Sale
from smart_mart.models.user import User
from smart_mart.models.user_permissions import UserPermissions


@pytest.fixture
def product(db):
    p = Product(
        name="Cashew Pack",
        sku="PERM-CASHEW",
        category="Dry Fruits",
        cost_price=Decimal("60.00"),
        selling_price=Decimal("100.00"),
        quantity=50,
    )
    db.session.add(p)
    db.session.commit()
    return p


def _staff(db, username: str, *, can_discount: bool) -> User:
    user = User(username=username, password_hash="x", role="staff")
    db.session.add(user)
    db.session.commit()
    perms = UserPermissions.get_or_create(user.id)
    perms.can_create_sale = True
    perms.can_give_discount = can_discount
    db.session.commit()
    return user


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def _post_sale(client, product, discount):
    return client.post(
        "/sales/create",
        data={
            "items[0][product_id]": str(product.id),
            "items[0][quantity]": "2",
            "items[0][unit_price]": "100",
            "payment_mode": "cash",
            "discount_amount": discount,
        },
        follow_redirects=True,
    )


def test_staff_without_permission_cannot_discount(client, db, product):
    _login(client, _staff(db, "no_disc", can_discount=False))

    resp = _post_sale(client, product, "50")

    assert resp.status_code == 200
    assert b"do not have permission to apply a discount" in resp.data
    # Sale rejected outright — no half-applied discount, no stock movement.
    assert db.session.execute(db.select(db.func.count(Sale.id))).scalar() == 0
    assert db.session.get(Product, product.id).quantity == 50


def test_staff_without_permission_can_still_sell_without_a_discount(client, db, product):
    _login(client, _staff(db, "no_disc_ok", can_discount=False))

    resp = _post_sale(client, product, "0")

    assert resp.status_code == 200
    sale = db.session.execute(db.select(Sale)).scalars().first()
    assert sale is not None
    assert float(sale.discount_amount) == 0.0
    assert db.session.get(Product, product.id).quantity == 48


def test_staff_with_permission_can_discount(client, db, product):
    _login(client, _staff(db, "yes_disc", can_discount=True))

    resp = _post_sale(client, product, "50")

    assert resp.status_code == 200
    sale = db.session.execute(db.select(Sale)).scalars().first()
    assert sale is not None
    assert float(sale.discount_amount) == 50.0
    assert float(sale.total_amount) == 150.0


def test_non_numeric_discount_is_rejected_not_a_500(client, db, product):
    """The field is free text in the form; garbage must not reach Decimal()."""
    _login(client, _staff(db, "junk_disc", can_discount=True))

    resp = _post_sale(client, product, "abc")

    assert resp.status_code == 200
    assert b"Discount must be a number" in resp.data
    assert db.session.execute(db.select(db.func.count(Sale.id))).scalar() == 0
