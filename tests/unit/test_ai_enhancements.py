from datetime import datetime, timezone
from decimal import Decimal

from smart_mart.extensions import db
from smart_mart.models.ai_enhancements import AIDecisionLog
from smart_mart.models.customer import Customer
from smart_mart.models.product import Product
from smart_mart.models.purchase_order import PurchaseOrder
from smart_mart.models.sale import Sale, SaleItem
from smart_mart.models.sale_return import SaleReturn
from smart_mart.models.stock_movement import StockMovement
from smart_mart.models.supplier import Supplier
from smart_mart.models.user import User
from smart_mart.services import (
    ai_growth_ops,
    ai_profit_leak,
    ai_simulation,
    competitor_pricing_service,
    customer_quality_service,
    loyalty_wallet_service,
    sales_manager,
    sync_service,
)
from smart_mart.services.schema_migrations import run_pending_migrations


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


def test_sync_conflict_preserves_newer_server_customer(db):
    customer = Customer(
        name="Hari",
        phone="9800009999",
        address="Server",
        updated_at=datetime.now(timezone.utc),
    )
    db.session.add(customer)
    db.session.commit()

    older_ts = datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat()
    result = sync_service.push_events(
        device_id="DEVICE-OLD",
        events=[
            {
                "entity_type": "customer",
                "operation": "upsert",
                "payload": {"name": "Hari", "phone": "9800000002", "address": "Offline"},
                "client_timestamp": older_ts,
            }
        ],
    )

    db.session.refresh(customer)
    assert result["conflicts"] == 1
    assert result["results"][0]["status"] == "conflict"
    assert customer.phone == "9800009999"
    assert customer.address == "Server"


def test_sale_rolls_back_if_loyalty_write_fails(db, monkeypatch):
    user = _seed_user("sales_admin")
    product = _seed_product("SKU-ROLLBACK", quantity=5)
    wallet = loyalty_wallet_service.get_or_create_wallet("Rollback Customer", "9800000010")
    wallet.points_balance = 10
    db.session.commit()

    def _boom(*args, **kwargs):
        raise RuntimeError("wallet write failed")

    monkeypatch.setattr(loyalty_wallet_service, "apply_sale_points", _boom)

    try:
        sales_manager.create_sale(
            items=[{"product_id": product.id, "quantity": 1, "unit_price": 120.0}],
            user_id=user.id,
            customer_name="Rollback Customer",
            customer_phone="9800000010",
            wallet_redeem_points=5,
        )
        assert False, "create_sale should fail when loyalty write fails"
    except RuntimeError as exc:
        assert "wallet write failed" in str(exc)

    db.session.expire_all()
    product_after = db.session.get(Product, product.id)
    wallet_after = db.session.get(type(wallet), wallet.id)
    assert db.session.execute(db.select(db.func.count(Sale.id))).scalar() == 0
    assert product_after.quantity == 5
    assert wallet_after.points_balance == 10


def test_versioned_schema_migrations_are_idempotent(app, db):
    with app.app_context():
        first_run = run_pending_migrations(app)
        second_run = run_pending_migrations(app)

    assert isinstance(first_run, list)
    assert second_run == []


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


def test_auto_replenishment_plan_and_draft_po_creation(db):
    admin = _seed_user("restock_admin")
    supplier = Supplier(name="Replenish Supplier")
    db.session.add(supplier)
    db.session.flush()
    product = Product(
        name="Fast Noodles",
        category="Food",
        sku="SKU-RESTOCK",
        cost_price=Decimal("70.00"),
        selling_price=Decimal("100.00"),
        quantity=2,
        supplier_id=supplier.id,
        reorder_point=12,
    )
    db.session.add(product)
    db.session.flush()

    for i in range(10):
        sale = Sale(
            user_id=admin.id,
            total_amount=Decimal("200.00"),
            sale_date=datetime.now(timezone.utc),
        )
        db.session.add(sale)
        db.session.flush()
        db.session.add(
            SaleItem(
                sale_id=sale.id,
                product_id=product.id,
                quantity=2,
                unit_price=Decimal("100.00"),
                cost_price=Decimal("70.00"),
                subtotal=Decimal("200.00"),
            )
        )
    db.session.commit()

    plan = ai_growth_ops.auto_replenishment_plan(lookback_days=30, safety_days=4, coverage_days=14)
    assert plan["total_products_to_restock"] >= 1
    assert plan["supplier_groups"]
    assert any(item["product_id"] == product.id for item in plan["supplier_groups"][0]["items"])

    result = ai_growth_ops.create_auto_draft_purchase_orders(
        user_id=admin.id,
        lookback_days=30,
        safety_days=4,
        coverage_days=14,
    )
    assert result["created_order_count"] >= 1
    created = db.session.execute(db.select(PurchaseOrder).where(PurchaseOrder.created_by == admin.id)).scalars().all()
    assert created


def test_price_optimizer_suggests_change_for_expiry_risk(db):
    admin = _seed_user("pricing_admin")
    supplier = Supplier(name="Pricing Supplier")
    db.session.add(supplier)
    db.session.flush()
    product = Product(
        name="Expiring Juice",
        category="Beverage",
        sku="SKU-PRICE",
        cost_price=Decimal("100.00"),
        selling_price=Decimal("140.00"),
        quantity=180,
        supplier_id=supplier.id,
    )
    product.expiry_date = datetime.now(timezone.utc).date()
    db.session.add(product)
    db.session.flush()

    sale = Sale(
        user_id=admin.id,
        total_amount=Decimal("140.00"),
        sale_date=datetime.now(timezone.utc),
    )
    db.session.add(sale)
    db.session.flush()
    db.session.add(
        SaleItem(
            sale_id=sale.id,
            product_id=product.id,
            quantity=1,
            unit_price=Decimal("140.00"),
            cost_price=Decimal("100.00"),
            subtotal=Decimal("140.00"),
        )
    )
    db.session.commit()

    result = ai_growth_ops.optimize_product_prices(lookback_days=30, max_adjustment_pct=10, min_margin_pct=8)
    target = next((s for s in result["suggestions"] if s["product_id"] == product.id), None)
    assert target is not None
    assert target["suggested_price"] <= target["current_price"]
    assert target["action"] in ("decrease", "keep")


def test_fraud_signal_detection_finds_high_risk_patterns(db):
    admin = _seed_user("fraud_admin")
    supplier = Supplier(name="Fraud Supplier")
    db.session.add(supplier)
    db.session.flush()
    product = Product(
        name="Flagged Item",
        category="General",
        sku="SKU-FRAUD",
        cost_price=Decimal("80.00"),
        selling_price=Decimal("120.00"),
        quantity=100,
        supplier_id=supplier.id,
    )
    db.session.add(product)
    db.session.flush()

    sales = []
    for _ in range(8):
        sale = Sale(
            user_id=admin.id,
            total_amount=Decimal("1000.00"),
            discount_amount=Decimal("150.00"),
            sale_date=datetime.now(timezone.utc),
        )
        db.session.add(sale)
        sales.append(sale)
    db.session.flush()

    for sale in sales[:4]:
        db.session.add(
            SaleReturn(
                sale_id=sale.id,
                processed_by=admin.id,
                refund_amount=Decimal("900.00"),
                refund_mode="cash",
            )
        )

    for _ in range(5):
        db.session.add(
            StockMovement(
                product_id=product.id,
                change_amount=-6,
                change_type="adjustment_out",
                created_by=admin.id,
                note="manual correction",
            )
        )
    db.session.commit()

    report = ai_profit_leak.detect_fraud_signals(days=30)
    assert report["overall_risk_score"] > 0
    assert report["suspicious_discount_patterns"]
    assert report["suspicious_return_patterns"]
    assert report["suspicious_stock_adjustments"]


def test_interactive_scenario_simulation_returns_impact(db):
    admin = _seed_user("scenario_admin")
    supplier = Supplier(name="Scenario Supplier")
    db.session.add(supplier)
    db.session.flush()
    product = Product(
        name="Scenario Product",
        category="General",
        sku="SKU-SCENARIO",
        cost_price=Decimal("90.00"),
        selling_price=Decimal("130.00"),
        quantity=120,
        supplier_id=supplier.id,
    )
    db.session.add(product)
    db.session.flush()

    for _ in range(6):
        sale = Sale(
            user_id=admin.id,
            total_amount=Decimal("260.00"),
            sale_date=datetime.now(timezone.utc),
        )
        db.session.add(sale)
        db.session.flush()
        db.session.add(
            SaleItem(
                sale_id=sale.id,
                product_id=product.id,
                quantity=2,
                unit_price=Decimal("130.00"),
                cost_price=Decimal("90.00"),
                subtotal=Decimal("260.00"),
            )
        )
    db.session.commit()

    scenario = ai_simulation.simulate_product_scenario(
        product_id=product.id,
        price_change_pct=-5,
        demand_change_pct=10,
        extra_expense=100,
        days=30,
    )
    assert "error" not in scenario
    assert "impact" in scenario
    assert "profit_change" in scenario["impact"]
