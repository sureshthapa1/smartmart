"""One-time migration — adds missing columns to existing tables.

Usage:
    python migrate.py
"""

from smart_mart.app import create_app
from smart_mart.extensions import db
from sqlalchemy import text

app = create_app("development")

with app.app_context():
    db.create_all()
    print("New tables created (if any).")

    with db.engine.connect() as conn:
        def safe_add(table, column, col_type):
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                conn.commit()
                print(f"  + {table}.{column}")
            except Exception as e:
                msg = str(e).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    print(f"  ~ {table}.{column} already exists")
                else:
                    print(f"  ! {table}.{column} ERROR: {e}")

        print("\n--- suppliers ---")
        safe_add("suppliers", "email", "VARCHAR(120)")
        safe_add("suppliers", "address", "VARCHAR(255)")

        print("\n--- sales ---")
        safe_add("sales", "invoice_number", "VARCHAR(30)")
        safe_add("sales", "customer_name", "VARCHAR(120)")
        safe_add("sales", "customer_address", "VARCHAR(255)")
        safe_add("sales", "customer_phone", "VARCHAR(50)")
        safe_add("sales", "payment_mode", "VARCHAR(20) DEFAULT 'cash'")
        safe_add("sales", "discount_amount", "NUMERIC(10,2) DEFAULT 0")
        safe_add("sales", "discount_note", "VARCHAR(120)")
        safe_add("sales", "credit_due_date", "DATE")
        safe_add("sales", "credit_collected", "BOOLEAN NOT NULL DEFAULT 0")

        print("\n--- products ---")
        safe_add("products", "image_filename", "VARCHAR(255)")
        safe_add("products", "unit", "VARCHAR(20) DEFAULT 'pcs'")

        print("\n--- expenses ---")
        # expenses table is created by db.create_all() above

    print("\nMigration complete.")

