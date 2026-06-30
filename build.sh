#!/usr/bin/env bash
set -e

echo "=== Python runtime ==="
python --version
pip --version

echo "=== Installing dependencies ==="
pip install -r requirements.txt

echo "=== Compiling translation catalogs (i18n) ==="
pybabel compile -d smart_mart/translations 2>&1 || echo "WARNING: translation compile failed — falling back to English"

echo "=== Initialising database ==="
python - <<'PYEOF'
import os, sys, traceback

os.environ.setdefault("FLASK_ENV", "production")

# Validate required env vars before doing anything
required = ["SECRET_KEY", "DATABASE_URL", "ADMIN_PASSWORD"]
missing = [k for k in required if not os.environ.get(k)]
if missing:
    print(f"ERROR: Missing required environment variables: {', '.join(missing)}")
    print("Set them in the Render dashboard under Environment.")
    sys.exit(1)

try:
    from smart_mart.app import create_app
    from smart_mart.extensions import db
    from sqlalchemy import text

    app = create_app("production")
    print("App created OK.")
except Exception as e:
    print(f"ERROR creating app: {e}")
    traceback.print_exc()
    sys.exit(1)

with app.app_context():
    # ── Create all tables ─────────────────────────────────────────────────
    try:
        db.create_all()
        print("Tables created.")
    except Exception as e:
        print(f"WARNING: db.create_all() failed (non-fatal): {e}")

    # ── Safe column additions (idempotent, PostgreSQL ADD COLUMN IF NOT EXISTS)
    def safe_add(conn, table, column, col_type):
        try:
            conn.execute(text(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type}"
            ))
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            # Log but don't fail — column may already exist or table may not exist yet

    try:
        with db.engine.connect() as conn:
            # ── Customers ─────────────────────────────────────────────────
            safe_add(conn, "customers", "birthday", "DATE")
            safe_add(conn, "customers", "email", "VARCHAR(120)")
            safe_add(conn, "customers", "updated_at", "TIMESTAMP")

            # ── Products ──────────────────────────────────────────────────
            safe_add(conn, "products", "reorder_point", "INTEGER DEFAULT 10")
            safe_add(conn, "products", "inventory_value", "NUMERIC(14,2) DEFAULT 0")
            safe_add(conn, "products", "is_active", "BOOLEAN DEFAULT true")
            safe_add(conn, "products", "barcode", "VARCHAR(80)")
            safe_add(conn, "products", "max_discount_pct", "NUMERIC(5,2)")
            safe_add(conn, "products", "tax_category", "VARCHAR(20) DEFAULT 'standard'")
            safe_add(conn, "products", "description", "TEXT")
            safe_add(conn, "products", "pack_size", "VARCHAR(40)")
            safe_add(conn, "products", "slug", "VARCHAR(160)")
            safe_add(conn, "products", "is_featured", "BOOLEAN DEFAULT false")
            safe_add(conn, "online_orders", "customer_email", "VARCHAR(120)")

            # ── Sales ─────────────────────────────────────────────────────
            safe_add(conn, "sales", "customer_id", "INTEGER REFERENCES customers(id)")
            safe_add(conn, "sales", "promotion_id", "INTEGER REFERENCES promotions(id)")
            safe_add(conn, "sales", "tax_rate", "NUMERIC(5,2) DEFAULT 0")
            safe_add(conn, "sales", "tax_amount", "NUMERIC(10,2) DEFAULT 0")
            safe_add(conn, "sales", "credit_due_date", "DATE")

            # ── Purchases ─────────────────────────────────────────────────
            safe_add(conn, "purchases", "tax_rate", "NUMERIC(5,2) DEFAULT 0")
            safe_add(conn, "purchases", "tax_amount", "NUMERIC(10,2) DEFAULT 0")

            # ── Stock movements ───────────────────────────────────────────
            safe_add(conn, "stock_movements", "stock_take_id", "INTEGER REFERENCES stock_takes(id)")

            # ── Shop settings ─────────────────────────────────────────────
            safe_add(conn, "shop_settings", "logo_filename", "VARCHAR(255)")
            safe_add(conn, "shop_settings", "logo_data", "TEXT")
            safe_add(conn, "shop_settings", "loyalty_points_per_rupee", "NUMERIC(8,4) DEFAULT 0.01")
            safe_add(conn, "shop_settings", "loyalty_rupee_per_point", "NUMERIC(8,4) DEFAULT 1.00")

            # ── AI retraining log ─────────────────────────────────────────
            for col in ["model_name VARCHAR(80)", "samples_used INTEGER",
                        "new_accuracy FLOAT", "improvement FLOAT", "error_message TEXT"]:
                name, typ = col.split(" ", 1)
                safe_add(conn, "ai_retraining_log", name, typ)

            # ── Customer risk scores ──────────────────────────────────────
            for col, typ in [
                ("risk_score", "INTEGER DEFAULT 0"),
                ("risk_tier", "VARCHAR(20) DEFAULT 'safe'"),
                ("override_tier", "VARCHAR(20)"),
                ("override_by", "INTEGER"),
                ("override_at", "TIMESTAMP"),
                ("last_computed_at", "TIMESTAMP"),
            ]:
                safe_add(conn, "customer_risk_scores", col, typ)

            # ── User permissions ──────────────────────────────────────────
            false_perms = [
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
                "can_view_bi_dashboard", "can_manage_bi_batches",
                "can_manage_offers", "can_void_sale",
            ]
            for col in false_perms:
                safe_add(conn, "user_permissions", col, "BOOLEAN DEFAULT false")

            true_perms = ["can_view_offers", "can_assign_offers", "can_apply_offers"]
            for col in true_perms:
                safe_add(conn, "user_permissions", col, "BOOLEAN DEFAULT true")

            # ── BI tables ─────────────────────────────────────────────────
            safe_add(conn, "bi_purchase_batch_items", "lot_number", "VARCHAR(80)")
            safe_add(conn, "bi_purchase_batch_items", "batch_expiry", "DATE")
            safe_add(conn, "bi_operating_expenses", "product_id", "INTEGER REFERENCES products(id)")
            safe_add(conn, "expenses", "bi_opex_id", "INTEGER REFERENCES bi_operating_expenses(id)")

            # ── Offers ────────────────────────────────────────────────────
            safe_add(conn, "offers", "start_date", "DATE")
            safe_add(conn, "offers", "end_date", "DATE")

            # ── Loyalty points expiry ─────────────────────────────────────
            safe_add(conn, "loyalty_wallet_transactions", "expires_at", "TIMESTAMP")
            safe_add(conn, "loyalty_wallet_transactions", "is_expired", "BOOLEAN DEFAULT false")

            # ── Product reviews moderation ────────────────────────────────
            safe_add(conn, "product_reviews", "is_approved", "BOOLEAN DEFAULT false")
            # ── Wishlist CustomerAccount FK ───────────────────────────────────
            safe_add(conn, "wishlist_items", "customer_account_id",
                     "INTEGER REFERENCES customer_accounts(id) ON DELETE SET NULL")

            # ── ShopSettings — social media + website URL fields ─────────────────
            safe_add(conn, "shop_settings", "facebook_url",    "VARCHAR(255)")
            safe_add(conn, "shop_settings", "instagram_url",   "VARCHAR(255)")
            safe_add(conn, "shop_settings", "twitter_url",     "VARCHAR(255)")
            safe_add(conn, "shop_settings", "tiktok_url",      "VARCHAR(255)")
            safe_add(conn, "shop_settings", "whatsapp_number", "VARCHAR(30)")
            safe_add(conn, "shop_settings", "website_url",     "VARCHAR(255)")

            # ── Knowledge base articles table (chatbot RAG) ──────────────────────
            try:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS knowledge_articles (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        title      VARCHAR(200) NOT NULL,
                        category   VARCHAR(80)  NOT NULL DEFAULT 'general',
                        keywords   TEXT,
                        body       TEXT NOT NULL,
                        is_active  BOOLEAN NOT NULL DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                conn.commit()
            except Exception:
                pass   # Table already exists on PostgreSQL

        print("Column migrations complete.")
    except Exception as e:
        print(f"WARNING: Column migrations failed (non-fatal): {e}")
        traceback.print_exc()

    # ── Run versioned schema migrations ───────────────────────────────────
    try:
        from smart_mart.services.schema_migrations import run_pending_migrations
        applied = run_pending_migrations(app)
        if applied:
            print(f"Schema migrations applied: {', '.join(applied)}")
        else:
            print("Schema migrations: all up to date.")
    except Exception as e:
        print(f"WARNING: Schema migrations failed (non-fatal): {e}")

    # ── Ensure all tables exist ───────────────────────────────────────────
    try:
        db.create_all()
        print("All tables ensured.")
    except Exception as e:
        print(f"WARNING: Final db.create_all() failed: {e}")

    # ── Admin user ────────────────────────────────────────────────────────
    try:
        from smart_mart.models.user import User
        from smart_mart.services.authenticator import hash_password

        admin_password = os.environ.get("ADMIN_PASSWORD")
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
    except Exception as e:
        print(f"ERROR setting up admin user: {e}")
        traceback.print_exc()
        sys.exit(1)

    # ── Shop settings ─────────────────────────────────────────────────────
    try:
        from smart_mart.models.shop_settings import ShopSettings
        ShopSettings.get()
        db.session.commit()
        print("Shop settings ready.")
    except Exception as e:
        print(f"WARNING: Shop settings init failed: {e}")

    # ── Expense sync backfill ─────────────────────────────────────────────
    try:
        from smart_mart.services.expense_sync import backfill as _expense_backfill
        n = _expense_backfill()
        if n:
            print(f"expense_sync backfill: {n} rows created.")
    except Exception as e:
        print(f"expense_sync backfill skipped: {e}")

    # ── AI product autofill — fill descriptions + images for all products ─
    try:
        from smart_mart.services.product_autofill import autofill_all_empty
        results = autofill_all_empty(limit=200)
        print(f"AI autofill: {results['updated']} products enriched, "
              f"{results['skipped']} already complete out of {results['total']} processed.")
    except Exception as e:
        print(f"AI autofill skipped (non-fatal): {e}")

    print("=== Database initialisation complete ===")

PYEOF

echo "=== Build complete ==="
