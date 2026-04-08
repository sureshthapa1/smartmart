"""One-time migration — adds missing columns to existing tables.

Usage:
    python migrate.py
"""

from smart_mart.app import create_app
from smart_mart.extensions import db
from sqlalchemy import text

app = create_app("development")

with app.app_context():
    # Create any brand new tables (e.g. categories)
    db.create_all()
    print("New tables created.")

    # Add missing columns to suppliers table
    with db.engine.connect() as conn:
        def safe_add(table, column, col_type):
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                conn.commit()
                print(f"Added {table}.{column}")
            except Exception as e:
                msg = str(e).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    print(f"{table}.{column} already exists, skipping.")
                else:
                    print(f"Error adding {table}.{column}: {e}")

        # suppliers
        safe_add("suppliers", "email", "VARCHAR(120)")
        safe_add("suppliers", "address", "VARCHAR(255)")

        # sales — customer info + invoice number
        safe_add("sales", "invoice_number", "VARCHAR(30)")
        safe_add("sales", "customer_name", "VARCHAR(120)")
        safe_add("sales", "customer_address", "VARCHAR(255)")
        safe_add("sales", "customer_phone", "VARCHAR(50)")
        safe_add("sales", "payment_mode", "VARCHAR(20) DEFAULT 'cash'")
        safe_add("sales", "discount_amount", "NUMERIC(10,2) DEFAULT 0")
        safe_add("sales", "discount_note", "VARCHAR(120)")

        # products — image and unit
        safe_add("products", "image_filename", "VARCHAR(255)")
        safe_add("products", "unit", "VARCHAR(20) DEFAULT 'pcs'")

        # online orders — new tables created via db.create_all() above

    print("Migration complete.")
