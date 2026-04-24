#!/usr/bin/env bash
# No set -e — we handle errors manually so migration warnings don't kill deploy

echo "=== Installing dependencies ==="
pip install -r requirements.txt || { echo "pip install failed"; exit 1; }

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
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type}"))
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass

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
        safe_add(conn, "shop_settings", "logo_data", "TEXT")
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
            safe_add(conn, "user_permissions", col, "BOOLEAN DEFAULT false")
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

        # ── New columns from high-priority upgrades ───────────────────────
        # BI / user permissions
        for col in ["can_view_bi_dashboard", "can_manage_bi_batches"]:
            safe_add(conn, "user_permissions", col, "BOOLEAN DEFAULT false")

        # Sales
        safe_add(conn, "sales", "customer_id", "INTEGER REFERENCES customers(id)")
        safe_add(conn, "sales", "promotion_id", "INTEGER REFERENCES promotions(id)")
        safe_add(conn, "sales", "tax_rate", "NUMERIC(5,2) DEFAULT 0")
        safe_add(conn, "sales", "tax_amount", "NUMERIC(10,2) DEFAULT 0")

        # Purchases
        safe_add(conn, "purchases", "tax_rate", "NUMERIC(5,2) DEFAULT 0")
        safe_add(conn, "purchases", "tax_amount", "NUMERIC(10,2) DEFAULT 0")

        # Products
        safe_add(conn, "products", "inventory_value", "NUMERIC(14,2) DEFAULT 0")

        # Stock movements
        safe_add(conn, "stock_movements", "stock_take_id", "INTEGER REFERENCES stock_takes(id)")

        # BI batch items lot tracking
        safe_add(conn, "bi_purchase_batch_items", "lot_number", "VARCHAR(80)")
        safe_add(conn, "bi_purchase_batch_items", "batch_expiry", "DATE")

        # BI operating expenses — product allocation
        safe_add(conn, "bi_operating_expenses", "product_id", "INTEGER REFERENCES products(id)")

        # Expense → BI sync link (must come AFTER bi_operating_expenses exists)
        safe_add(conn, "expenses", "bi_opex_id", "INTEGER REFERENCES bi_operating_expenses(id)")

    print("Migration complete.")

    # Create recurring_expenses table if not exists (db.create_all handles it)
    db.create_all()
    print("All tables ensured.")

    from smart_mart.models.user import User
    from smart_mart.services.authenticator import hash_password

    # SECURITY: Admin password MUST be set via ADMIN_PASSWORD env var
    admin_password = os.environ.get("ADMIN_PASSWORD")
    if not admin_password:
        print("ERROR: ADMIN_PASSWORD environment variable is not set.")
        print("Set it in your deployment platform (Render, Heroku, etc.) before deploying.")
        sys.exit(1)

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
        # Update password on every deploy (allows password rotation)
        admin.password_hash = hash_password(admin_password)
        db.session.commit()
        print(f"Admin password updated: {admin_username}")

    print(f"Login: username={admin_username}  password=<set via ADMIN_PASSWORD env var>")

    from smart_mart.models.shop_settings import ShopSettings
    ShopSettings.get()
    db.session.commit()
    print("Shop settings ready.")

    # Expense → BI sync backfill (safe, non-fatal)
    try:
        from smart_mart.services.expense_sync import backfill as _expense_backfill
        n = _expense_backfill()
        if n:
            print(f"expense_sync backfill: {n} rows created.")
    except Exception as _e:
        print(f"expense_sync backfill skipped: {_e}")

    # Sync customer visit counts from actual sales
    from smart_mart.models.customer import Customer
    from smart_mart.models.sale import Sale
    from sqlalchemy import func as _f
    customers = db.session.execute(db.select(Customer)).scalars().all()
    synced = 0
    for c in customers:
        actual = db.session.execute(
            db.select(_f.count(Sale.id))
            .where(_f.lower(Sale.customer_name) == c.name.lower())
        ).scalar() or 0
        if actual != c.visit_count:
            c.visit_count = actual
            synced += 1
    if synced:
        db.session.commit()
        print(f"Synced visit counts for {synced} customer(s).")
EOF

echo "=== Build complete ==="
