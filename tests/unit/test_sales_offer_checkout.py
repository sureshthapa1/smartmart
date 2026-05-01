from datetime import date, timedelta

import pytest

from smart_mart.extensions import db
from smart_mart.models.customer import Customer
from smart_mart.models.offer import CustomerOffer, Offer
from smart_mart.models.product import Product
from smart_mart.models.user import User
from smart_mart.services import offer_service, sales_manager


def _user():
    user = User(username="offer_cashier", password_hash="hash", role="admin")
    db.session.add(user)
    db.session.flush()
    return user


def _product(name="Tea", sku="OFFER-TEA"):
    product = Product(
        name=name,
        sku=sku,
        category="Grocery",
        cost_price=50,
        selling_price=100,
        quantity=10,
    )
    db.session.add(product)
    db.session.flush()
    return product


def _customer(name="Maya Rai"):
    customer = Customer(name=name, phone="9800000000", address="Kathmandu")
    db.session.add(customer)
    db.session.flush()
    return customer


def _offer(customer, **kwargs):
    offer = Offer(
        title=kwargs.get("title", "Loyalty 10%"),
        offer_type=kwargs.get("offer_type", "percentage"),
        discount_value=kwargs.get("discount_value", 10),
        min_purchase_amount=kwargs.get("min_purchase_amount"),
        product_id=kwargs.get("product_id"),
        valid_days=30,
        usage_limit=1,
        status="active",
    )
    db.session.add(offer)
    db.session.flush()
    customer_offer = CustomerOffer(
        customer_id=customer.id,
        offer_id=offer.id,
        assigned_date=date.today(),
        expiry_date=date.today() + timedelta(days=7),
    )
    db.session.add(customer_offer)
    db.session.flush()
    return offer, customer_offer


def test_create_sale_marks_applied_customer_offer_used(db):
    user = _user()
    product = _product()
    customer = _customer()
    _, customer_offer = _offer(customer)
    db.session.commit()

    sale = sales_manager.create_sale(
        [{"product_id": product.id, "quantity": 1, "unit_price": 100}],
        user_id=user.id,
        customer_name=customer.name,
        customer_phone=customer.phone,
        discount_amount=10,
        applied_customer_offer_ids=[customer_offer.id],
    )

    refreshed_offer = db.session.get(CustomerOffer, customer_offer.id)
    refreshed_product = db.session.get(Product, product.id)
    assert float(sale.total_amount) == 90.0
    assert refreshed_offer.status == CustomerOffer.STATUS_USED
    assert refreshed_offer.applied_sale_id == sale.id
    assert refreshed_offer.usage_count == 1
    assert refreshed_product.quantity == 9


def test_create_sale_rejects_offer_discount_not_in_submitted_discount(db):
    user = _user()
    product = _product(name="Rice", sku="OFFER-RICE")
    customer = _customer(name="Nima Lama")
    _, customer_offer = _offer(customer)
    db.session.commit()

    with pytest.raises(ValueError, match="larger than the submitted sale discount"):
        sales_manager.create_sale(
            [{"product_id": product.id, "quantity": 1, "unit_price": 100}],
            user_id=user.id,
            customer_name=customer.name,
            customer_phone=customer.phone,
            discount_amount=0,
            applied_customer_offer_ids=[customer_offer.id],
        )

    assert db.session.get(CustomerOffer, customer_offer.id).status == CustomerOffer.STATUS_UNUSED
    assert db.session.get(Product, product.id).quantity == 10


def test_active_offer_payload_includes_product_scope(db):
    product = _product(name="Coffee", sku="OFFER-COFFEE")
    customer = _customer(name="Sita Karki")
    _, customer_offer = _offer(
        customer,
        title="Coffee deal",
        offer_type="product_based",
        discount_value=20,
        product_id=product.id,
    )
    db.session.commit()

    payload = offer_service.get_active_offers_for_customer(customer.id)

    row = next(item for item in payload if item["customer_offer_id"] == customer_offer.id)
    assert row["scope"] == "product"
    assert row["product_id"] == product.id
    assert row["product_name"] == "Coffee"
