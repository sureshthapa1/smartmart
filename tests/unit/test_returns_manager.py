from decimal import Decimal

import pytest

from smart_mart.extensions import db
from smart_mart.models.product import Product
from smart_mart.models.supplier import Supplier
from smart_mart.models.user import User
from smart_mart.services import returns_manager, sales_manager


def _create_user(username: str = "cashier") -> User:
    user = User(username=username, password_hash="hash", role="admin")
    db.session.add(user)
    db.session.commit()
    return user


def _create_product(quantity: int = 20, sku: str = "RICE-001") -> Product:
    supplier = Supplier(name="Acme Supplies")
    db.session.add(supplier)
    db.session.flush()
    product = Product(
        name="Rice Bag",
        category="Groceries",
        sku=sku,
        cost_price=Decimal("80.00"),
        selling_price=Decimal("120.00"),
        quantity=quantity,
        supplier_id=supplier.id,
    )
    db.session.add(product)
    db.session.commit()
    return product


def test_create_return_restores_stock_and_records_refund(app, db):
    with app.app_context():
        user = _create_user()
        product = _create_product(sku="RICE-001")
        sale = sales_manager.create_sale(
            [{"product_id": product.id, "quantity": 3, "unit_price": 120}],
            user_id=user.id,
            customer_name="Sita",
        )

        returned = returns_manager.create_return(
            sale_id=sale.id,
            user_id=user.id,
            item_quantities=[{"sale_item_id": sale.items[0].id, "quantity": 2}],
            refund_mode="cash",
            reason="Damaged pack",
        )

        refreshed_product = db.session.get(Product, product.id)
        assert float(returned.refund_amount) == 240.0
        assert refreshed_product.quantity == 19
        assert len(returned.items) == 1
        assert returned.items[0].quantity == 2


def test_create_return_blocks_more_than_remaining_quantity(app, db):
    with app.app_context():
        user = _create_user("manager")
        product = _create_product(quantity=10, sku="RICE-002")
        sale = sales_manager.create_sale(
            [{"product_id": product.id, "quantity": 4, "unit_price": 120}],
            user_id=user.id,
        )

        returns_manager.create_return(
            sale_id=sale.id,
            user_id=user.id,
            item_quantities=[{"sale_item_id": sale.items[0].id, "quantity": 3}],
            refund_mode="cash",
        )

        with pytest.raises(ValueError, match="only 1 remaining"):
            returns_manager.create_return(
                sale_id=sale.id,
                user_id=user.id,
                item_quantities=[{"sale_item_id": sale.items[0].id, "quantity": 2}],
                refund_mode="cash",
            )
