#!/usr/bin/env bash
set -e

echo "=== Installing dependencies ==="
pip install -r requirements.txt

echo "=== Initialising database ==="
python - <<'EOF'
import os, sys
os.environ.setdefault("FLASK_ENV", "production")
from smart_mart.app import create_app
from smart_mart.extensions import db
from sqlalchemy import text

app = create_app("production")
with app.app_context():
    db.create_all()
    print("Tables created.")

    # Run all column migrations safely
    def safe_add(conn, table, column, col_type):
        try:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            conn.commit()
            print(f"  + {table}.{column}")
        except Exception as e:
            msg = str(e).lower()
            if "already exists" in msg or "duplicate" in msg or "column" in msg:
                pass  # already exists
            else:
                print(f"  ! {table}.{column}: {e}")

    with db.engine.connect() as conn:
        # Customers
        safe_add(conn, "customers", "birthday", "DATE")
        safe_add(conn, "customers", "email", "VARCHAR(120)")
        # Products
        safe_add(conn, "products", "reorder_point", "INTEGER DEFAULT 10")
        # AI retraining log
        safe_add(conn, "ai_retraining_log", "model_name", "VARCHAR(80)")
        safe_add(conn, "ai_retraining_log", "samples_used", "INTEGER")
        safe_add(conn, "ai_retraining_log", "new_accuracy", "FLOAT")
        safe_add(conn, "ai_retraining_log", "improvement", "FLOAT")
        safe_add(conn, "ai_retraining_log", "error_message", "TEXT")
        # Shop settings
        safe_add(conn, "shop_settings", "logo_filename", "VARCHAR(255)")
        safe_add(conn, "shop_settings", "loyalty_points_per_rupee", "NUMERIC(8,4) DEFAULT 0.01")
        safe_add(conn, "shop_settings", "loyalty_rupee_per_point", "NUMERIC(8,4) DEFAULT 1.00")
        # User permissions — all new columns
        for col in [
            "can_manage_categories", "can_manage_variants", "can_print_labels",
            "can_view_stock_take", "can_manage_stock_take", "can_view_customer_statement",
            "can_view_supplier_returns", "can_manage_supplier_returns",
            "can_view_purchase_orders", "can_manage_purchase_orders",
            "can_view_customers", "can_manage_customers",
            "can_view_expenses", "can_manage_expenses",
            "can_view_reports", "can_view_sales_report", "can_view_profit_report",
            "can_view_stock_report", "can_view_credit_report",
            "can_view_promotions", "can_manage_promotions",
            "can_view_transfers", "can_manage_transfers",
            "can_view_ai_insights", "can_view_advisor",
            "can_manage_credits", "can_manage_cash_session",
        ]:
            safe_add(conn, "user_permissions", col, "BOOLEAN DEFAULT FALSE")
        # Customer risk scores
        for col, typ in [
            ("risk_score", "INTEGER DEFAULT 0"),
            ("risk_tier", "VARCHAR(20) DEFAULT 'safe'"),
            ("override_tier", "VARCHAR(20)"),
            ("override_by", "INTEGER"),
            ("override_at", "TIMESTAMP"),
            ("last_computed_at", "TIMESTAMP"),
        ]:
            safe_add(conn, "customer_risk_scores", col, typ)

    print("Migration complete.")

    from smart_mart.models.user import User
    from smart_mart.services.authenticator import hash_password

    admin_password = os.environ.get("ADMIN_PASSWORD", "Admin@1234")
    admin_username = os.environ.get("ADMIN_USERNAME", "admin")

    admin = db.session.execute(
        db.select(User).where(User.username == admin_username)
    ).scalar_one_or_none()

    if not admin:
        admin = User(
            username=admin_username,
            password_hash=hash_password(admin_password),
            role="admin",
        )
        db.session.add(admin)
        db.session.commit()
        print(f"Admin user created: {admin_username}")
    else:
        admin.password_hash = hash_password(admin_password)
        db.session.commit()
        print(f"Admin password updated: {admin_username}")

    print(f"Login: username={admin_username}  password={admin_password}")

    from smart_mart.models.shop_settings import ShopSettings
    ShopSettings.get()
    db.session.commit()
    print("Shop settings ready.")
EOF

echo "=== Build complete ==="
