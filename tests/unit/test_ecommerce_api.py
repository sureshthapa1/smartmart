from smart_mart.extensions import db
from smart_mart.models.ecommerce import StockReservation
from smart_mart.models.online_order import OnlineOrder
from smart_mart.models.product import Product


def _product(quantity=10):
    product = Product(
        name="Premium Almonds",
        category="Dry Fruits",
        sku="ALM-001",
        cost_price=500,
        selling_price=850,
        quantity=quantity,
        low_stock_threshold=2,
        is_active=True,
    )
    db.session.add(product)
    db.session.commit()
    return product


def _order_payload(product, quantity=2):
    return {
        "customer": {
            "name": "Sita Sharma",
            "phone": "9800000000",
            "email": "sita@example.com",
            "address": "Kathmandu",
            "area": "Lazimpat",
        },
        "items": [
            {
                "product_id": product.id,
                "quantity": quantity,
            }
        ],
        "payment": {
            "method": "cod",
            "status": "pending",
        },
        "delivery_charge": 100,
        "reservation_minutes": 30,
    }


def test_products_include_available_stock(client):
    _product(quantity=7)

    response = client.get("/api/products")

    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert data["products"][0]["available_quantity"] == 7


def test_website_order_creates_pos_online_order_and_reservation(client):
    product = _product(quantity=10)

    response = client.post(
        "/api/orders/create",
        json=_order_payload(product, quantity=2),
        headers={"Idempotency-Key": "cart-123"},
    )

    assert response.status_code == 201
    data = response.get_json()
    assert data["order"]["status"] == "pending"
    assert data["order"]["amounts"]["grand_total"] == 1800.0

    order = db.session.execute(db.select(OnlineOrder)).scalar_one()
    reservation = db.session.execute(db.select(StockReservation)).scalar_one()
    product = db.session.get(Product, product.id)

    assert order.order_source == "website"
    assert reservation.status == "active"
    assert reservation.quantity == 2
    assert product.quantity == 10


def test_confirming_order_consumes_reservation_and_deducts_stock(client):
    product = _product(quantity=10)
    create_response = client.post("/api/orders/create", json=_order_payload(product, quantity=3))
    order_number = create_response.get_json()["order"]["order_number"]

    response = client.put(
        "/api/orders/update-status",
        json={"order_number": order_number, "status": "confirmed"},
    )

    assert response.status_code == 200
    product = db.session.get(Product, product.id)
    reservation = db.session.execute(db.select(StockReservation)).scalar_one()

    assert product.quantity == 7
    assert reservation.status == "consumed"
    assert response.get_json()["order"]["status"] == "confirmed"


def test_idempotency_key_prevents_duplicate_orders(client):
    product = _product(quantity=10)
    payload = _order_payload(product, quantity=2)

    first = client.post("/api/orders/create", json=payload, headers={"Idempotency-Key": "same-cart"})
    second = client.post("/api/orders/create", json=payload, headers={"Idempotency-Key": "same-cart"})

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.get_json()["duplicate"] is True
    assert db.session.execute(db.select(db.func.count(OnlineOrder.id))).scalar() == 1


def test_active_reservations_prevent_overselling(client):
    product = _product(quantity=3)
    first = client.post("/api/orders/create", json=_order_payload(product, quantity=2))
    second = client.post("/api/orders/create", json=_order_payload(product, quantity=2))

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.get_json()["details"]["available_quantity"] == 1
