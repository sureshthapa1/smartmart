from datetime import datetime, timezone
from decimal import Decimal

from smart_mart.extensions import db
from smart_mart.models.ai_enhancements import AIDecisionLog
from smart_mart.models.customer import Customer
from smart_mart.models.product import Product
from smart_mart.models.supplier import Supplier
from smart_mart.models.user import User
from smart_mart.services import (
    competitor_pricing_service,
    customer_quality_service,
    loyalty_wallet_service,
    sync_service,
)


def _seed_user(username: str = "admin") -> User:
    user = User(username=username, password_hash="hash", role="admin")
    db.session.add(user)
    db.session.commit()
    return user


def _seed_product(sku: str = "SKU-100", quantity: int = 10) -> Product:
    supplier = Supplier(name=f"Supplier-{sku}")
    db.session.add(supplier)
    db.session.flush()
    product = Product(
        name=f"Product-{sku}",
        category="General",
        sku=sku,
        cost_price=Decimal("80.00"),
        selling_price=Decimal("120.00"),
        quantity=quantity,
        supplier_id=supplier.id,
    )
    db.session.add(product)
    db.session.commit()
    return product


def test_loyalty_wallet_earn_and_redeem(db):
    wallet = loyalty_wallet_service.get_or_create_wallet("Ram", "9800000001")
    db.session.commit()
    assert wallet is not None
    assert wallet.points_balance == 0

    loyalty_wallet_service.apply_sale_points(wallet, sale_id=1, final_amount_paid=1200, redeemed_points=0)
    db.session.commit()
    assert wallet.points_balance == 12
    assert wallet.tier == "Silver"

    preview = loyalty_wallet_service.preview_redeem(wallet, requested_points=5, gross_total=500)
    assert preview["redeemed_points"] == 5
    assert preview["discount"] == 5.0
    assert preview["payable_total"] == 495.0


def test_duplicate_detection_creates_flag_and_log(db):
    _seed_user("dup_admin")
    c1 = Customer(name="Sita Devi", phone="980-111-2222", address="A")
    c2 = Customer(name="Sita Devi", phone="9801112222", address="B")
    db.session.add_all([c1, c2])
    db.session.commit()

    flags = customer_quality_service.detect_duplicates(trigger_user_id=1)
    assert len(flags) >= 1
    assert float(flags[0].confidence) > 0.9

    log = db.session.execute(
        db.select(AIDecisionLog).where(AIDecisionLog.decision_type == "duplicate_customer_detection")
    ).scalars().first()
    assert log is not None


def test_sync_push_and_pull_roundtrip(db):
    result = sync_service.push_events(
        device_id="DEVICE-1",
        events=[
            {
                "entity_type": "customer",
                "operation": "upsert",
                "payload": {"name": "Hari", "phone": "9800000002", "address": "Kathmandu"},
                "client_timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
    )
    assert result["applied"] == 1
    pulled = sync_service.pull_events(device_id="DEVICE-1", since_event_id=0)
    assert pulled["last_event_id"] >= 1
    assert len(pulled["events"]) >= 1


def test_competitor_suggestion_generates_log(db):
    _seed_user("price_admin")
    product = _seed_product("SKU-200")
    competitor_pricing_service.add_competitor_price(
        product_id=product.id,
        competitor_name="Nearby Mart",
        competitor_price=105.0,
        captured_by_user_id=1,
    )
    suggestion = competitor_pricing_service.generate_pricing_suggestion(product.id)
    assert suggestion["suggested_price"] > 0
    assert suggestion["product_id"] == product.id

    log = db.session.execute(
        db.select(AIDecisionLog).where(AIDecisionLog.decision_type == "competitor_pricing_suggestion")
    ).scalars().first()
    assert log is not None
