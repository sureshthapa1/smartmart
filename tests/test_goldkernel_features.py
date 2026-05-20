from datetime import date, datetime, timedelta, timezone

from smart_mart.extensions import db
from smart_mart.models.audit_log import AuditLog
from smart_mart.models.ai_enhancements import LoyaltyWallet
from smart_mart.models.bundle import Bundle, BundleItem
from smart_mart.models.customer import Customer
from smart_mart.models.login_attempt import LoginAttempt
from smart_mart.models.product import Product
from smart_mart.models.sale import Sale
from smart_mart.models.shop_settings import ShopSettings
from smart_mart.models.supplier_price_record import SupplierPriceRecord
from smart_mart.models.user import User
from smart_mart.services.authenticator import hash_password
from smart_mart.services import sales_manager
from smart_mart.utils.expiry_check import check_cart_for_expiry
from smart_mart.utils.low_stock import get_low_stock_alerts
from smart_mart.utils.nepali_date import ad_to_bs
from smart_mart.utils.payment_reports import daily_payment_reconciliation
from smart_mart.utils.vat_invoice import generate_vat_invoice


def _admin(username="admin_feature"):
    user = User(username=username, password_hash=hash_password("password123"), role="admin")
    db.session.add(user)
    db.session.commit()
    return user


def _login(client, user):
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True


def _product(name="Almonds", sku="ALM-001", quantity=1000, expiry_date=None):
    product = Product(
        name=name,
        sku=sku,
        category="Dry Fruits",
        cost_price=1,
        selling_price=2,
        quantity=quantity,
        unit="g",
        expiry_date=expiry_date,
        low_stock_threshold=500,
    )
    db.session.add(product)
    db.session.commit()
    return product


def test_login_rate_limit_returns_429_on_sixth_attempt(client, db):
    from smart_mart.extensions import limiter

    limiter.reset()
    _admin("rate_admin")
    for _ in range(5):
        response = client.post("/auth/login", data={"username": "rate_admin", "password": "wrong"})
        assert response.status_code == 200

    response = client.post("/auth/login", data={"username": "rate_admin", "password": "wrong"})
    assert response.status_code == 429
    assert b"Too many login attempts" in response.data


def test_correct_credentials_on_fifth_attempt_still_succeed(client, db):
    from smart_mart.extensions import limiter

    limiter.reset()
    _admin("fifth_admin")
    for _ in range(4):
        client.post("/auth/login", data={"username": "fifth_admin", "password": "wrong"})

    response = client.post("/auth/login", data={"username": "fifth_admin", "password": "password123"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard/")


def test_failed_login_attempt_is_recorded(client, db):
    from smart_mart.extensions import limiter

    limiter.reset()
    _admin("audit_admin")
    client.post("/auth/login", data={"username": "audit_admin", "password": "bad"})

    attempt = db.session.execute(db.select(LoginAttempt)).scalar_one()
    assert attempt.username == "audit_admin"
    assert attempt.successful is False
    assert attempt.ip_address


def test_customer_search_uses_legacy_sale_history(client, db):
    user = _admin("customer_search_admin")
    _login(client, user)
    db.session.add_all([
        Sale(
            user_id=user.id,
            total_amount=100,
            customer_name="Legacy Buyer",
            customer_phone="9801112222",
            customer_address="Dhangadhi",
            payment_mode="cash",
            payment_method="cash",
        ),
        Sale(
            user_id=user.id,
            total_amount=200,
            customer_name="Legacy Buyer",
            customer_phone="9801112222",
            customer_address="Dhangadhi",
            payment_mode="cash",
            payment_method="cash",
        ),
    ])
    db.session.commit()

    response = client.get("/api/customer-search?q=Legacy")
    assert response.status_code == 200
    data = response.get_json()
    assert data[0]["name"] == "Legacy Buyer"
    assert data[0]["phone"] == "9801112222"
    assert data[0]["address"] == "Dhangadhi"
    assert data[0]["visits"] == 2
    assert data[0]["id"] is None


def test_customer_search_does_not_attach_wrong_phone_owner(client, db):
    user = _admin("customer_search_phone_admin")
    _login(client, user)
    other = Customer(name="Different Customer", phone="9809998888", address="Other")
    db.session.add(other)
    db.session.flush()
    db.session.add(Sale(
        user_id=user.id,
        total_amount=100,
        customer_name="Legacy Phone Owner",
        customer_phone="9809998888",
        customer_address="Dhangadhi",
        payment_mode="cash",
        payment_method="cash",
    ))
    db.session.commit()

    response = client.get("/api/customer-search?q=Legacy Phone")
    assert response.status_code == 200
    data = response.get_json()
    assert data[0]["name"] == "Legacy Phone Owner"
    assert data[0]["phone"] == "9809998888"
    assert data[0]["id"] is None


def test_problem_sections_render_with_existing_business_data(client, db):
    user = _admin("section_smoke_admin")
    _login(client, user)
    customer = Customer(name="Credit Customer", phone="9802223333", address="Dhangadhi")
    db.session.add(customer)
    db.session.flush()
    db.session.add(LoyaltyWallet(customer_id=customer.id, points_balance=150, tier="Gold"))
    db.session.add(Sale(
        user_id=user.id,
        total_amount=500,
        customer_id=customer.id,
        customer_name=customer.name,
        customer_phone=customer.phone,
        payment_mode="credit",
        payment_method="credit",
        credit_due_date=date.today() + timedelta(days=7),
    ))
    db.session.commit()

    for path in ("/admin/notifications", "/operations/credits", "/reports/customer-spend"):
        response = client.get(path)
        assert response.status_code == 200, path


def test_expired_cart_item_blocks_sale(client, db):
    user = _admin("expiry_admin")
    _login(client, user)
    product = _product(expiry_date=date.today() - timedelta(days=1))

    response = client.post("/sales/create", data={
        "items[0][product_id]": str(product.id),
        "items[0][quantity]": "10",
        "items[0][unit_price]": "2",
        "payment_mode": "cash",
    })

    assert response.status_code == 302
    assert db.session.execute(db.select(db.func.count(Sale.id))).scalar() == 0


def test_near_expiry_cart_warns_but_sale_proceeds(client, db):
    user = _admin("near_expiry_admin")
    _login(client, user)
    product = _product(name="Cashews", sku="CAS-001", expiry_date=date.today() + timedelta(days=3))

    response = client.post("/sales/create", data={
        "items[0][product_id]": str(product.id),
        "items[0][quantity]": "10",
        "items[0][unit_price]": "2",
        "payment_mode": "cash",
    })

    assert response.status_code == 302
    assert db.session.execute(db.select(db.func.count(Sale.id))).scalar() == 1


def test_clean_cart_has_no_expiry_issues(db):
    product = _product(name="Dates", sku="DAT-001", expiry_date=date.today() + timedelta(days=30))
    item = type("CartItem", (), {
        "product_id": product.id,
        "product_name": product.name,
        "expiry_date": product.expiry_date,
    })()
    assert check_cart_for_expiry([item]) == []


def test_sale_records_payment_method_and_reconciliation_totals(db):
    user = _admin("payment_admin")
    product = _product(name="Pistachios", sku="PIS-001")
    sale = sales_manager.create_sale(
        [{"product_id": product.id, "quantity": 10, "unit_price": 5}],
        user_id=user.id,
        payment_method="fonepay",
    )

    data = daily_payment_reconciliation(date.today())
    fonepay = next(row for row in data["records"] if row["method"] == "fonepay")
    assert sale.payment_method == "fonepay"
    assert fonepay["sale_count"] == 1
    assert fonepay["total_collected"] == 50


def test_vat_invoice_pdf_and_route(client, db):
    user = _admin("invoice_admin")
    _login(client, user)
    product = _product(name="Walnuts", sku="WAL-001")
    sale = sales_manager.create_sale(
        [{"product_id": product.id, "quantity": 10, "unit_price": 5}],
        user_id=user.id,
        payment_method="cash",
    )
    settings = ShopSettings.get()
    pdf = generate_vat_invoice(sale, settings)
    assert pdf.startswith(b"%PDF")

    login = client.post("/auth/login", data={"username": user.username, "password": "password123"})
    assert login.status_code == 302
    response = client.get(f"/sales/{sale.id}/invoice/pdf")
    assert response.status_code == 200, response.headers.get("Location")
    assert response.mimetype == "application/pdf"


def test_bs_date_known_conversion():
    assert ad_to_bs(date(2025, 4, 14)) == (2082, 1, 1)


def test_low_stock_alerts_use_product_threshold(db):
    low = _product(name="Figs", sku="FIG-001", quantity=200)
    _product(name="Apricots", sku="APR-001", quantity=900)

    alerts = get_low_stock_alerts()
    assert alerts[0]["id"] == low.id
    assert alerts[0]["severity"] == "low"


def test_bundle_stock_check_and_sell_route(client, db):
    user = _admin("bundle_admin")
    _login(client, user)
    almonds = _product(name="Bundle Almonds", sku="B-ALM", quantity=1000)
    cashews = _product(name="Bundle Cashews", sku="B-CAS", quantity=700)
    bundle = Bundle(name="Daily Health Box", price=750, season_tag="General", is_active=True)
    bundle.items.append(BundleItem(product_id=almonds.id, quantity=200))
    bundle.items.append(BundleItem(product_id=cashews.id, quantity=100))
    db.session.add(bundle)
    db.session.commit()

    stock = client.get(f"/bundles/{bundle.id}/stock_check?units=2").get_json()
    assert stock["can_sell"] is True

    response = client.post(f"/bundles/{bundle.id}/sell", data={"units": "2", "payment_method": "cash"})
    assert response.status_code == 302
    assert db.session.get(Product, almonds.id).quantity == 600
    assert db.session.get(Product, cashews.id).quantity == 500
    assert db.session.execute(db.select(Sale).where(Sale.sale_type == "bundle")).scalar_one().total_amount == 1500


def test_bundle_sell_blocked_when_stock_insufficient(client, db):
    user = _admin("bundle_block_admin")
    _login(client, user)
    product = _product(name="Short Almonds", sku="S-ALM", quantity=50)
    bundle = Bundle(name="Dashain Premium Hamper", price=1850, season_tag="Dashain", is_active=True)
    bundle.items.append(BundleItem(product_id=product.id, quantity=200))
    db.session.add(bundle)
    db.session.commit()

    response = client.post(f"/bundles/{bundle.id}/sell", data={"units": "1"})
    assert response.status_code == 302
    assert db.session.get(Product, product.id).quantity == 50
    assert db.session.execute(db.select(db.func.count(Sale.id))).scalar() == 0


def test_ai_chat_missing_key_returns_helpful_500(client, db, monkeypatch):
    user = _admin("ai_admin")
    _login(client, user)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    response = client.post("/ai/chat/ask", json={"message": "How were sales today?", "history": []})
    assert response.status_code == 500
    assert "ANTHROPIC_API_KEY" in response.get_json()["error"]


def test_ai_context_builder_non_empty(db):
    from smart_mart.blueprints.ai_chat.routes import build_business_context

    assert "Today's revenue" in build_business_context()


def test_ai_chat_login_required(client, db):
    response = client.post("/ai/chat/ask", json={"message": "hi"})
    assert response.status_code == 302


def test_waste_record_route_deducts_stock(client, db):
    user = _admin("waste_admin")
    _login(client, user)
    product = _product(name="Waste Dates", sku="W-DAT", quantity=300)

    response = client.post("/waste/record", data={"product_id": product.id, "quantity": "50", "reason": "damaged"})
    assert response.status_code == 302
    assert db.session.get(Product, product.id).quantity == 250


def test_supplier_price_route_updates_product_cost(client, db):
    user = _admin("supplier_price_admin")
    _login(client, user)
    product = _product(name="Supplier Cashews", sku="SP-CAS", quantity=1000)

    response = client.post(f"/inventory/products/{product.id}/price", data={"cost_price": "12.50", "supplier_name": "Test Supplier"})
    assert response.status_code == 302
    assert float(db.session.get(Product, product.id).cost_price) == 12.5
    assert db.session.execute(db.select(db.func.count(SupplierPriceRecord.id))).scalar() == 1


def test_sales_target_progress_endpoint(client, db):
    from smart_mart.models.sales_target import SalesTarget

    user = _admin("target_admin")
    _login(client, user)
    db.session.add(SalesTarget(user_id=user.id, target_date=date.today(), target_type="daily", amount=1000))
    db.session.commit()

    response = client.get("/targets/progress")
    assert response.status_code == 200
    assert response.get_json()["has_target"] is True


def test_flask_db_upgrade_cli_runs(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["db", "upgrade"])
    assert result.exit_code == 0
