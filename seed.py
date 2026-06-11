"""Seed script for a local SmartMart database.

Usage:
    python seed.py
"""

from decimal import Decimal

from smart_mart.app import create_app
from smart_mart.extensions import db
from smart_mart.models.bundle import Bundle, BundleItem
from smart_mart.models.category import Category
from smart_mart.models.customer import Customer
from smart_mart.models.product import Product
from smart_mart.models.shop_settings import ShopSettings
from smart_mart.models.user import User
from smart_mart.services.authenticator import hash_password


app = create_app("development")


DEFAULT_CATEGORIES = [
    "Food & Grocery",
    "Beverages",
    "Dairy & Eggs",
    "Meat & Fish",
    "Fruits & Vegetables",
    "Dry Fruits",
    "Seeds",
    "Gift Hampers",
    "Snacks & Bakery",
    "Sweets & Chocolates",
    "Grains & Pulses",
    "Oils & Fats",
    "Spices & Condiments",
    "Household & Cleaning",
    "Personal Care & Hygiene",
    "Baby Products",
    "Medicine & Health",
    "Electronics & Accessories",
    "Stationery",
    "Tobacco & Misc",
]


SAMPLE_PRODUCTS = [
    ("ALM-GK", "Almonds", "Dry Fruits", Decimal("1.10"), Decimal("1.55"), 6000),
    ("CAS-GK", "Cashews", "Dry Fruits", Decimal("1.25"), Decimal("1.75"), 5000),
    ("PIS-GK", "Pistachios", "Dry Fruits", Decimal("2.20"), Decimal("2.95"), 4000),
    ("DAT-GK", "Premium Dates", "Dry Fruits", Decimal("0.85"), Decimal("1.25"), 4500),
    ("WAL-GK", "Walnuts", "Dry Fruits", Decimal("1.45"), Decimal("2.10"), 3500),
    ("SEED-GK", "Mixed Seeds", "Seeds", Decimal("0.75"), Decimal("1.15"), 3000),
]


SAMPLE_BUNDLES = [
    (
        "Dashain Premium Hamper",
        "Festive dry fruit hamper for Dashain gifting.",
        Decimal("1850.00"),
        "Dashain",
        True,
        [("ALM-GK", Decimal("500")), ("CAS-GK", Decimal("300")), ("PIS-GK", Decimal("200")), ("DAT-GK", Decimal("200"))],
    ),
    (
        "Daily Health Box",
        "Everyday wellness box with nuts and seeds.",
        Decimal("750.00"),
        "General",
        False,
        [("ALM-GK", Decimal("200")), ("WAL-GK", Decimal("100")), ("SEED-GK", Decimal("100"))],
    ),
    (
        "Wedding Gift Pack",
        "Premium pack for wedding and family gifting.",
        Decimal("2200.00"),
        "Wedding",
        True,
        [("PIS-GK", Decimal("300")), ("CAS-GK", Decimal("300")), ("DAT-GK", Decimal("200"))],
    ),
]


SAMPLE_CUSTOMERS = [
    ("Asha Thapa", "9800000001", 320, "silver", Decimal("15000.00")),
    ("Bikram Rana", "9800000002", 3100, "gold", Decimal("42000.00")),
    ("Sunita Joshi", "9800000003", 11200, "platinum", Decimal("125000.00")),
]


def ensure_admin():
    import os as _os
    admin_password = _os.environ.get("ADMIN_PASSWORD", "")
    flask_env = _os.environ.get("FLASK_ENV", "development")

    # In production, refuse to fall back to a hardcoded default password.
    if flask_env == "production" and not admin_password:
        raise RuntimeError(
            "ADMIN_PASSWORD environment variable must be set in production. "
            "Run: export ADMIN_PASSWORD=<your-secure-password>"
        )

    # In development, use a clear default and warn loudly.
    if not admin_password:
        admin_password = "admin123"
        print(
            "WARNING: Using default password admin123. "
            "Set ADMIN_PASSWORD env var before deploying to production."
        )

    admin_username = _os.environ.get("ADMIN_USERNAME", "admin")
    admin = db.session.execute(db.select(User).filter_by(username=admin_username)).scalar_one_or_none()
    if admin is None:
        admin = User(username=admin_username, password_hash=hash_password(admin_password), role="admin")
        db.session.add(admin)
        db.session.commit()
        print(f"Admin user created: username={admin_username}")
    else:
        print("Admin user already exists.")


def ensure_categories():
    added = 0
    for name in DEFAULT_CATEGORIES:
        exists = db.session.execute(db.select(Category).filter_by(name=name)).scalar_one_or_none()
        if exists is None:
            db.session.add(Category(name=name))
            added += 1
    if added:
        db.session.commit()
        print(f"{added} default categories added.")
    else:
        print("Categories already exist.")


def ensure_shop_settings():
    settings = ShopSettings.get()
    settings.name = "Goldkernel Dryfruits and Treats"
    settings.shop_name = "Goldkernel Dryfruits and Treats"
    settings.address = "Dhangadhi, Sudurpashchim, Nepal"
    settings.phone = ""
    settings.pan_number = "TBD"
    settings.vat_number = "TBD"
    settings.owner_name = settings.owner_name or ""
    settings.vat_enabled = True
    settings.vat_rate = Decimal("13.00")
    settings.footer_note = "Thank you for shopping at Goldkernel!"
    settings.loyalty_points_per_rupee = Decimal("0.10")
    settings.loyalty_rupee_per_point = Decimal("0.10")
    db.session.commit()
    print("Shop settings ready.")


def ensure_products():
    products = {}
    added = 0
    for sku, name, category, cost_price, selling_price, quantity in SAMPLE_PRODUCTS:
        product = db.session.execute(db.select(Product).filter_by(sku=sku)).scalar_one_or_none()
        if product is None:
            product = Product(
                sku=sku,
                name=name,
                category=category,
                cost_price=cost_price,
                selling_price=selling_price,
                quantity=quantity,
                unit="g",
                low_stock_threshold=500,
                is_active=True,
            )
            db.session.add(product)
            added += 1
        products[sku] = product
    db.session.commit()
    print(f"{added} sample products added." if added else "Sample products already exist.")
    return products


def ensure_bundles(products):
    added = 0
    for name, description, price, season_tag, is_seasonal, items in SAMPLE_BUNDLES:
        bundle = db.session.execute(db.select(Bundle).filter_by(name=name)).scalar_one_or_none()
        if bundle is None:
            bundle = Bundle(name=name)
            db.session.add(bundle)
            added += 1
        bundle.description = description
        bundle.price = price
        bundle.season_tag = season_tag
        bundle.is_seasonal = is_seasonal
        bundle.is_active = True
        bundle.items = [
            BundleItem(product_id=products[sku].id, quantity=quantity)
            for sku, quantity in items
        ]
    db.session.commit()
    print(f"{added} sample bundles added." if added else "Sample bundles refreshed.")


def ensure_customers():
    added = 0
    for name, phone, points, tier, total_spent in SAMPLE_CUSTOMERS:
        customer = db.session.execute(db.select(Customer).filter_by(phone=phone)).scalar_one_or_none()
        if customer is None:
            customer = Customer(name=name, phone=phone, address="Dhangadhi")
            db.session.add(customer)
            added += 1
        customer.loyalty_points = points
        customer.loyalty_tier = tier
        customer.total_spent = total_spent
    db.session.commit()
    print(f"{added} sample loyalty customers added." if added else "Sample loyalty customers refreshed.")


with app.app_context():
    db.create_all()
    print("Database tables created/updated.")

    ensure_admin()
    ensure_categories()
    ensure_shop_settings()
    seeded_products = ensure_products()
    ensure_bundles(seeded_products)
    ensure_customers()
