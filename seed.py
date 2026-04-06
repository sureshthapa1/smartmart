"""Seed script — creates DB tables and the initial admin user.

Usage:
    python seed.py
"""

from smart_mart.app import create_app
from smart_mart.extensions import db
from smart_mart.services.authenticator import hash_password
from smart_mart.models.user import User
from smart_mart.models.category import Category

app = create_app("development")

with app.app_context():
    # Create all tables (including new ones like categories)
    db.create_all()
    print("Database tables created/updated.")

    # Create default admin user
    if not db.session.execute(db.select(User).filter_by(username="admin")).scalar_one_or_none():
        admin = User(username="admin", password_hash=hash_password("admin123"), role="admin")
        db.session.add(admin)
        db.session.commit()
        print("Admin user created: username=admin, password=admin123")
    else:
        print("Admin user already exists.")

    # Seed default categories
    default_categories = [
        "Food & Grocery", "Beverages", "Dairy & Eggs", "Meat & Fish",
        "Fruits & Vegetables", "Snacks & Bakery", "Sweets & Chocolates",
        "Grains & Pulses", "Oils & Fats", "Spices & Condiments",
        "Household & Cleaning", "Personal Care & Hygiene", "Baby Products",
        "Medicine & Health", "Electronics & Accessories", "Stationery",
        "Tobacco & Misc",
    ]
    added = 0
    for name in default_categories:
        if not db.session.execute(db.select(Category).filter_by(name=name)).scalar_one_or_none():
            db.session.add(Category(name=name))
            added += 1
    if added:
        db.session.commit()
        print(f"{added} default categories added.")
    else:
        print("Categories already exist.")
