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

        print("\n--- dismissed_alerts ---")
        # dismissed_alerts table is created by db.create_all() above

        print("\n--- user_permissions (new columns) ---")
        safe_add("user_permissions", "can_view_returns", "BOOLEAN NOT NULL DEFAULT 1")
        safe_add("user_permissions", "can_create_return", "BOOLEAN NOT NULL DEFAULT 0")
        safe_add("user_permissions", "can_view_online_orders", "BOOLEAN NOT NULL DEFAULT 1")
        safe_add("user_permissions", "can_manage_online_orders", "BOOLEAN NOT NULL DEFAULT 0")

        print("\n--- shop_settings (new columns) ---")
        safe_add("shop_settings", "vat_enabled", "BOOLEAN NOT NULL DEFAULT 0")
        safe_add("shop_settings", "vat_rate", "NUMERIC(5,2) DEFAULT 13.00")
        safe_add("shop_settings", "vat_number", "VARCHAR(50)")
        safe_add("shop_settings", "currency_symbol", "VARCHAR(10) DEFAULT 'NPR'")
        safe_add("shop_settings", "low_stock_threshold", "INTEGER DEFAULT 10")

    print("\nMigration complete.")


# ── New features migration (Features 1-10) ────────────────────────────────────
with app.app_context():
    db.create_all()
    print("\nNew feature tables created (if any).")

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

        print("\n--- stock_movements (new types) ---")
        # change_type column already exists; new values: damage, loss, theft, expiry, supplier_return
        print("  ~ change_type supports new values: damage, loss, theft, expiry, supplier_return")

        print("\n--- promotions ---")
        print("  (created by db.create_all)")

        print("\n--- audit_logs ---")
        print("  (created by db.create_all)")

        print("\n--- supplier_returns / supplier_return_items ---")
        print("  (created by db.create_all)")

        print("\n--- stock_takes / stock_take_items ---")
        print("  (created by db.create_all)")

        print("\n--- backup_logs ---")
        print("  (created by db.create_all)")

    print("\nFeature migration complete.")

# ── Data sync fixes migration ─────────────────────────────────────────────────
with app.app_context():
    db.create_all()
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

        print("\n--- sale_items (historical cost) ---")
        safe_add("sale_items", "cost_price", "NUMERIC(10,2)")

    print("\nData sync migration complete.")

# ── Items 1-8 migration ───────────────────────────────────────────────────────
with app.app_context():
    db.create_all()
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

        print("\n--- shop_settings (logo) ---")
        safe_add("shop_settings", "logo_filename", "VARCHAR(255)")

    print("\nItems 1-8 migration complete.")

# ── AI model columns migration ────────────────────────────────────────────────
with app.app_context():
    db.create_all()
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

        print("\n--- ai_retraining_log (new columns) ---")
        safe_add("ai_retraining_log", "model_name", "VARCHAR(80)")
        safe_add("ai_retraining_log", "models_retrained", "TEXT")
        safe_add("ai_retraining_log", "samples_used", "INTEGER")
        safe_add("ai_retraining_log", "new_accuracy", "FLOAT")
        safe_add("ai_retraining_log", "improvement", "FLOAT")
        safe_add("ai_retraining_log", "error_message", "TEXT")

        print("\n--- customer_risk_scores ---")
        safe_add("customer_risk_scores", "risk_score", "INTEGER DEFAULT 0")
        safe_add("customer_risk_scores", "risk_tier", "VARCHAR(20) DEFAULT 'safe'")
        safe_add("customer_risk_scores", "override_tier", "VARCHAR(20)")
        safe_add("customer_risk_scores", "override_by", "INTEGER")
        safe_add("customer_risk_scores", "override_at", "DATETIME")
        safe_add("customer_risk_scores", "last_computed_at", "DATETIME")

    print("\nAI columns migration complete.")

# ── Features 3,5,6,7,8,10,11,12 migration ────────────────────────────────────
with app.app_context():
    db.create_all()
    with db.engine.connect() as conn:
        def safe_add(table, column, col_type):
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                conn.commit()
                print(f"  + {table}.{column}")
            except Exception as e:
                msg = str(e).lower()
                if "duplicate" in msg or "already exists" in msg:
                    print(f"  ~ {table}.{column} already exists")
                else:
                    print(f"  ! {table}.{column} ERROR: {e}")

        print("\n--- customers (birthday, email) ---")
        safe_add("customers", "birthday", "DATE")
        safe_add("customers", "email", "VARCHAR(120)")

        print("\n--- products (reorder_point) ---")
        safe_add("products", "reorder_point", "INTEGER DEFAULT 10")

    print("\nFeatures migration complete.")
