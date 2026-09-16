"""Regression tests for stock accounting on variant sales, voids, and returns.

create_sale deducts stock from ProductVariant for a variant line, but the
reversal paths (delete_sale, returns_manager.create_return) both credited the
parent Product instead — inventing parent units the shop never had while the
sold variant units stayed missing. These tests pin the reversal to the same
bucket the deduction came from.
"""

from decimal import Decimal

import pytest

from smart_mart.extensions import db as _db
from smart_mart.models.product import Product
from smart_mart.models.product_variant import ProductVariant
from smart_mart.models.user import User
from smart_mart.services import returns_manager, sales_manager


def _user(username: str = "cashier") -> User:
    user = User(username=username, password_hash="hash", role="admin")
    _db.session.add(user)
    _db.session.commit()
    return user


def _product_with_variant(
    parent_qty: int = 50,
    variant_qty: int = 20,
    sku: str = "TEA-001",
) -> tuple[Product, ProductVariant]:
    """A parent product holding loose stock plus one packaged variant.

    Both buckets are non-zero and different so a credit landing in the wrong
    one is unambiguous in the assertions.
    """
    product = Product(
        name="Green Tea",
        category="Beverages",
        sku=sku,
        cost_price=Decimal("80.00"),
        selling_price=Decimal("120.00"),
        quantity=parent_qty,
    )
    _db.session.add(product)
    _db.session.flush()
    variant = ProductVariant(
        product_id=product.id,
        variant_name="500g Pack",
        sku=f"{sku}-500G",
        cost_price=Decimal("90.00"),
        selling_price=Decimal("150.00"),
        quantity=variant_qty,
    )
    _db.session.add(variant)
    _db.session.commit()
    return product, variant


def test_variant_sale_deducts_variant_not_parent(app, db):
    with app.app_context():
        user = _user()
        product, variant = _product_with_variant(sku="TEA-A")

        sales_manager.create_sale(
            [{"variant_id": variant.id, "quantity": 3, "unit_price": 150}],
            user_id=user.id,
        )

        assert db.session.get(ProductVariant, variant.id).quantity == 17
        assert db.session.get(Product, product.id).quantity == 50


def test_delete_sale_restores_variant_stock_not_parent(app, db):
    """Voiding a variant sale must credit the variant, leaving the parent alone."""
    with app.app_context():
        user = _user("voider")
        product, variant = _product_with_variant(sku="TEA-B")

        sale = sales_manager.create_sale(
            [{"variant_id": variant.id, "quantity": 4, "unit_price": 150}],
            user_id=user.id,
        )
        assert db.session.get(ProductVariant, variant.id).quantity == 16

        sales_manager.delete_sale(sale.id)

        # Back to where it started — and the parent never moved in either direction.
        assert db.session.get(ProductVariant, variant.id).quantity == 20
        assert db.session.get(Product, product.id).quantity == 50


def test_delete_sale_nets_off_already_returned_units(app, db):
    """A partly-returned sale must not have those units credited twice.

    Sale.returns cascades away with the sale, so delete_sale is the last point
    at which an existing return can be accounted for. Crediting the full line
    quantity here would invent the returned units a second time.
    """
    with app.app_context():
        user = _user("netter")
        product, variant = _product_with_variant(parent_qty=50, variant_qty=20, sku="TEA-C")

        sale = sales_manager.create_sale(
            [{"variant_id": variant.id, "quantity": 5, "unit_price": 150}],
            user_id=user.id,
        )
        assert db.session.get(ProductVariant, variant.id).quantity == 15

        # Customer returns 2 of the 5 — variant is back up to 17.
        returns_manager.create_return(
            sale_id=sale.id,
            user_id=user.id,
            item_quantities=[{"sale_item_id": sale.items[0].id, "quantity": 2}],
            refund_mode="cash",
        )
        assert db.session.get(ProductVariant, variant.id).quantity == 17

        # Voiding the sale should credit only the 3 still outstanding.
        sales_manager.delete_sale(sale.id)
        assert db.session.get(ProductVariant, variant.id).quantity == 20
        assert db.session.get(Product, product.id).quantity == 50


def test_create_return_restores_variant_stock_not_parent(app, db):
    with app.app_context():
        user = _user("returner")
        product, variant = _product_with_variant(sku="TEA-D")

        sale = sales_manager.create_sale(
            [{"variant_id": variant.id, "quantity": 6, "unit_price": 150}],
            user_id=user.id,
        )
        assert db.session.get(ProductVariant, variant.id).quantity == 14

        returned = returns_manager.create_return(
            sale_id=sale.id,
            user_id=user.id,
            item_quantities=[{"sale_item_id": sale.items[0].id, "quantity": 2}],
            refund_mode="cash",
            reason="Wrong size",
        )

        assert float(returned.refund_amount) == 300.0
        assert db.session.get(ProductVariant, variant.id).quantity == 16
        assert db.session.get(Product, product.id).quantity == 50
        # The audit row still points at the parent product, which is what the
        # returns dashboard groups by.
        assert returned.items[0].product_id == product.id


def test_mixed_cart_reversal_hits_both_buckets_independently(app, db):
    """A cart with a loose parent line and a variant line reverses cleanly."""
    with app.app_context():
        user = _user("mixer")
        product, variant = _product_with_variant(parent_qty=30, variant_qty=12, sku="TEA-E")

        sale = sales_manager.create_sale(
            [
                {"product_id": product.id, "quantity": 5, "unit_price": 120},
                {"variant_id": variant.id, "quantity": 2, "unit_price": 150},
            ],
            user_id=user.id,
        )
        assert db.session.get(Product, product.id).quantity == 25
        assert db.session.get(ProductVariant, variant.id).quantity == 10

        sales_manager.delete_sale(sale.id)
        assert db.session.get(Product, product.id).quantity == 30
        assert db.session.get(ProductVariant, variant.id).quantity == 12
