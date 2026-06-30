from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import inspect, text

from ..extensions import db
from ..models.schema_migration import SchemaMigrationRecord


MigrationStep = tuple[str, str, Callable[[object], None]]


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    inspector = inspect(conn)
    try:
        columns = inspector.get_columns(table_name)
    except Exception:
        return False
    return any(col["name"] == column_name for col in columns)


def _table_exists(conn, table_name: str) -> bool:
    inspector = inspect(conn)
    try:
        return inspector.has_table(table_name)
    except Exception:
        return False


def _safe_add_column(conn, table_name: str, column_name: str, column_sql: str) -> None:
    if not _table_exists(conn, table_name):
        return
    if _column_exists(conn, table_name, column_name):
        return
    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}"))


def _safe_exec(conn, sql: str) -> None:
    """Execute a DDL statement, silently ignoring errors (e.g. already exists)."""
    try:
        conn.execute(text(sql))
    except Exception:
        pass


def _migration_steps() -> list[MigrationStep]:
    return [
        (
            "2026_04_15_customers_profile_columns",
            "Ensure customer profile timestamps and contact columns exist.",
            lambda conn: (
                _safe_add_column(conn, "customers", "birthday", "DATE"),
                _safe_add_column(conn, "customers", "email", "VARCHAR(120)"),
                _safe_add_column(conn, "customers", "updated_at", "TIMESTAMP"),
            ),
        ),
        (
            "2026_04_15_products_reorder_point",
            "Ensure products.reorder_point exists.",
            lambda conn: _safe_add_column(conn, "products", "reorder_point", "INTEGER DEFAULT 10"),
        ),
        (
            "2026_04_22_products_inventory_value",
            "Ensure products.inventory_value exists for valuation snapshots.",
            lambda conn: _safe_add_column(conn, "products", "inventory_value", "NUMERIC(14,2) DEFAULT 0"),
        ),
        (
            "2026_04_15_shop_settings_loyalty_and_branding",
            "Ensure shop settings branding and loyalty columns exist.",
            lambda conn: (
                _safe_add_column(conn, "shop_settings", "logo_filename", "VARCHAR(255)"),
                _safe_add_column(conn, "shop_settings", "logo_data", "TEXT"),
                _safe_add_column(conn, "shop_settings", "loyalty_points_per_rupee", "NUMERIC(8,4) DEFAULT 0.01"),
                _safe_add_column(conn, "shop_settings", "loyalty_rupee_per_point", "NUMERIC(8,4) DEFAULT 1.00"),
            ),
        ),
        (
            "2026_04_15_ai_retraining_columns",
            "Ensure AI retraining log fields exist.",
            lambda conn: (
                _safe_add_column(conn, "ai_retraining_log", "model_name", "VARCHAR(80)"),
                _safe_add_column(conn, "ai_retraining_log", "samples_used", "INTEGER"),
                _safe_add_column(conn, "ai_retraining_log", "new_accuracy", "FLOAT"),
                _safe_add_column(conn, "ai_retraining_log", "improvement", "FLOAT"),
                _safe_add_column(conn, "ai_retraining_log", "error_message", "TEXT"),
            ),
        ),
        (
            "2026_04_15_customer_risk_scores_columns",
            "Ensure customer risk score fields exist.",
            lambda conn: (
                _safe_add_column(conn, "customer_risk_scores", "risk_score", "INTEGER DEFAULT 0"),
                _safe_add_column(conn, "customer_risk_scores", "risk_tier", "VARCHAR(20) DEFAULT 'safe'"),
                _safe_add_column(conn, "customer_risk_scores", "override_tier", "VARCHAR(20)"),
                _safe_add_column(conn, "customer_risk_scores", "override_by", "INTEGER"),
                _safe_add_column(conn, "customer_risk_scores", "override_at", "TIMESTAMP"),
                _safe_add_column(conn, "customer_risk_scores", "last_computed_at", "TIMESTAMP"),
            ),
        ),
        (
            "2026_04_15_user_permissions_columns",
            "Ensure extended user permission columns exist.",
            lambda conn: (
                _safe_add_column(conn, "user_permissions", "can_manage_categories", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_manage_variants", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_print_labels", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_view_stock_take", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_manage_stock_take", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_view_customer_statement", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_view_supplier_returns", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_manage_supplier_returns", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_view_purchase_orders", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_manage_purchase_orders", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_view_customers", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_manage_customers", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_view_expenses", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_manage_expenses", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_view_reports", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_view_sales_report", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_view_profit_report", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_view_stock_report", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_view_credit_report", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_view_promotions", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_manage_promotions", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_view_transfers", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_manage_transfers", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_view_ai_insights", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_view_advisor", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_manage_credits", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_manage_cash_session", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_view_bi_dashboard", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_manage_bi_batches", "BOOLEAN DEFAULT false"),
            ),
        ),
        # ── New migrations ────────────────────────────────────────────────────
        (
            "2026_04_22_sale_customer_id",
            "Add customer_id FK to sales table.",
            lambda conn: _safe_add_column(conn, "sales", "customer_id", "INTEGER REFERENCES customers(id)"),
        ),
        (
            "2026_04_22_sale_promotion_id",
            "Add promotion_id FK to sales table.",
            lambda conn: _safe_add_column(conn, "sales", "promotion_id", "INTEGER REFERENCES promotions(id)"),
        ),
        (
            "2026_04_22_stock_movement_stock_take_id",
            "Add stock_take_id FK to stock_movements table.",
            lambda conn: _safe_add_column(conn, "stock_movements", "stock_take_id", "INTEGER REFERENCES stock_takes(id)"),
        ),
        (
            "2026_04_22_sale_tax_fields",
            "Add tax_rate and tax_amount to sales table.",
            lambda conn: (
                _safe_add_column(conn, "sales", "tax_rate", "NUMERIC(5,2) DEFAULT 0"),
                _safe_add_column(conn, "sales", "tax_amount", "NUMERIC(10,2) DEFAULT 0"),
            ),
        ),
        (
            "2026_04_22_purchase_tax_fields",
            "Add tax_rate and tax_amount to purchases table.",
            lambda conn: (
                _safe_add_column(conn, "purchases", "tax_rate", "NUMERIC(5,2) DEFAULT 0"),
                _safe_add_column(conn, "purchases", "tax_amount", "NUMERIC(10,2) DEFAULT 0"),
            ),
        ),
        (
            "2026_04_22_credit_notes_table",
            "Ensure credit_notes table exists (created by db.create_all).",
            lambda conn: None,  # db.create_all() handles this cross-DB
        ),
        (
            "2026_04_22_bi_batch_items_lot_tracking",
            "Add lot_number and batch_expiry to bi_purchase_batch_items.",
            lambda conn: (
                _safe_add_column(conn, "bi_purchase_batch_items", "lot_number", "VARCHAR(80)"),
                _safe_add_column(conn, "bi_purchase_batch_items", "batch_expiry", "DATE"),
            ),
        ),
        (
            "2026_04_22_bi_opex_product_id",
            "Add product_id to bi_operating_expenses for direct product allocation.",
            lambda conn: _safe_add_column(
                conn, "bi_operating_expenses", "product_id", "INTEGER REFERENCES products(id)"
            ),
        ),
        # ── High-priority upgrades ────────────────────────────────────────────
        (
            "2026_04_22_idempotency_keys_table",
            "Ensure idempotency_keys table exists (created by db.create_all).",
            lambda conn: None,  # db.create_all() handles this cross-DB
        ),
        (
            "2026_04_22_financial_periods_table",
            "Ensure financial_periods table exists (created by db.create_all).",
            lambda conn: None,  # db.create_all() handles this cross-DB
        ),
        (
            "2026_04_22_perf_indexes",
            "Add performance indexes on report-heavy fields.",
            lambda conn: (
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_sale_items_cost ON sale_items(cost_price)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_products_qty ON products(quantity)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_products_category ON products(category)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_expenses_date ON expenses(expense_date)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_stock_movements_product ON stock_movements(product_id)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_stock_movements_type ON stock_movements(change_type)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_purchases_date ON purchases(purchase_date)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_purchases_supplier_id ON purchases(supplier_id)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_purchases_created_by ON purchases(created_by)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_purchase_items_purchase_id ON purchase_items(purchase_id)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_purchase_items_product_id ON purchase_items(product_id)"),
            ),
        ),
        (
            "2026_04_22_expense_bi_opex_id",
            "Add bi_opex_id FK to expenses for automatic BI sync.",
            lambda conn: _safe_add_column(
                conn, "expenses", "bi_opex_id",
                "INTEGER REFERENCES bi_operating_expenses(id)"
            ),
        ),
        # ── Customer Retention & Offer System ────────────────────────────────
        (
            "2026_05_01_offers_tables",
            "Ensure offers, customer_offers, offer_notifications tables exist (db.create_all handles).",
            lambda conn: None,
        ),
        (
            "2026_05_01_user_permissions_offers",
            "Add offer permission columns to user_permissions.",
            lambda conn: (
                _safe_add_column(conn, "user_permissions", "can_view_offers", "BOOLEAN DEFAULT true"),
                _safe_add_column(conn, "user_permissions", "can_manage_offers", "BOOLEAN DEFAULT false"),
                _safe_add_column(conn, "user_permissions", "can_assign_offers", "BOOLEAN DEFAULT true"),
                _safe_add_column(conn, "user_permissions", "can_apply_offers", "BOOLEAN DEFAULT true"),
            ),
        ),
        (
            "2026_05_01_customers_total_spent",
            "Add total_spent column to customers for fast segmentation.",
            lambda conn: _safe_add_column(conn, "customers", "total_spent", "NUMERIC(12,2) DEFAULT 0"),
        ),
        (
            "2026_05_01_offer_indexes",
            "Add performance indexes for offer queries.",
            lambda conn: (
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_customer_offers_customer ON customer_offers(customer_id)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_customer_offers_status ON customer_offers(status)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_customer_offers_expiry ON customer_offers(expiry_date)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_offers_status ON offers(status)"),
            ),
        ),
        (
            "2026_05_01_offers_scheduling",
            "Add start_date and end_date scheduling columns to offers.",
            lambda conn: (
                _safe_add_column(conn, "offers", "start_date", "DATE"),
                _safe_add_column(conn, "offers", "end_date", "DATE"),
            ),
        ),
        (
            "2026_06_01_product_is_active",
            "Add is_active flag to products for discontinuing items.",
            lambda conn: _safe_add_column(conn, "products", "is_active", "BOOLEAN DEFAULT true"),
        ),
        (
            "2026_06_01_product_barcode",
            "Add barcode column to products for EAN-13/UPC scanner support.",
            lambda conn: _safe_add_column(conn, "products", "barcode", "VARCHAR(80)"),
        ),
        (
            "2026_06_01_product_discount_tax",
            "Add max_discount_pct and tax_category to products.",
            lambda conn: (
                _safe_add_column(conn, "products", "max_discount_pct", "NUMERIC(5,2)"),
                _safe_add_column(conn, "products", "tax_category", "VARCHAR(20) DEFAULT 'standard'"),
            ),
        ),
        (
            "2026_06_15_loyalty_points_expiry",
            "Add expires_at and is_expired to loyalty_wallet_transactions.",
            lambda conn: (
                _safe_add_column(conn, "loyalty_wallet_transactions", "expires_at", "TIMESTAMP"),
                _safe_add_column(conn, "loyalty_wallet_transactions", "is_expired", "BOOLEAN DEFAULT false"),
            ),
        ),
        (
            "2026_06_15_user_permissions_void_sale",
            "Add can_void_sale permission for cashier void workflow.",
            lambda conn: _safe_add_column(conn, "user_permissions", "can_void_sale", "BOOLEAN DEFAULT false"),
        ),
        (
            "2026_05_16_goldkernel_feature_pack_columns",
            "Add Goldkernel feature pack columns for payments, loyalty, settings, and low stock.",
            lambda conn: (
                _safe_add_column(conn, "products", "low_stock_threshold", "INTEGER DEFAULT 500"),
                _safe_add_column(conn, "sales", "payment_method", "VARCHAR(20) DEFAULT 'cash'"),
                _safe_add_column(conn, "sales", "sale_type", "VARCHAR(20) DEFAULT 'regular'"),
                _safe_add_column(conn, "customers", "loyalty_points", "INTEGER DEFAULT 0"),
                _safe_add_column(conn, "customers", "loyalty_tier", "VARCHAR(20) DEFAULT 'silver'"),
                _safe_add_column(conn, "customers", "total_spent", "NUMERIC(12,2) DEFAULT 0"),
                _safe_add_column(conn, "shop_settings", "name", "VARCHAR(120) DEFAULT 'Goldkernel Dryfruits and Treats'"),
                _safe_add_column(conn, "shop_settings", "owner_name", "VARCHAR(120)"),
            ),
        ),
        (
            "2026_05_16_goldkernel_feature_pack_tables",
            "Ensure feature pack tables exist (db.create_all handles model tables).",
            lambda conn: None,
        ),
        (
            "2026_05_29_purchase_indexes",
            "Add missing indexes on purchases and purchase_items tables.",
            lambda conn: (
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_purchases_supplier_id ON purchases(supplier_id)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_purchases_created_by ON purchases(created_by)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_purchase_items_purchase_id ON purchase_items(purchase_id)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_purchase_items_product_id ON purchase_items(product_id)"),
            ),
        ),
        (
            "2026_06_03_user_commission_rate",
            "Add commission_rate column to users table for staff commission tracking.",
            lambda conn: _safe_exec(
                conn,
                "ALTER TABLE users ADD COLUMN commission_rate NUMERIC(5,2) NOT NULL DEFAULT 0.00"
            ),
        ),
        (
            "2026_06_03_sale_commission_amount",
            "Add commission_amount column to sales table.",
            lambda conn: _safe_exec(
                conn,
                "ALTER TABLE sales ADD COLUMN commission_amount NUMERIC(10,2) NOT NULL DEFAULT 0.00"
            ),
        ),
        (
            "2026_06_07_customer_accounts_table",
            "Ensure customer_accounts table exists for storefront login.",
            lambda conn: None,  # db.create_all() handles this
        ),
        (
            "2026_06_08_product_description",
            "Add description column to products for storefront display.",
            lambda conn: _safe_add_column(conn, "products", "description", "TEXT"),
        ),
        (
            "2026_06_08_product_pack_size",
            "Add pack_size column to products for weight/size display on storefront.",
            lambda conn: _safe_add_column(conn, "products", "pack_size", "VARCHAR(40)"),
        ),
        (
            "2026_06_08_product_slug",
            "Add slug column to products for SEO-friendly URLs.",
            lambda conn: _safe_add_column(conn, "products", "slug", "VARCHAR(160)"),
        ),
        (
            "2026_06_08_product_is_featured",
            "Add is_featured flag to products for storefront hero section.",
            lambda conn: _safe_add_column(conn, "products", "is_featured", "BOOLEAN DEFAULT false"),
        ),
        (
            "2026_06_08_stock_notifications_table",
            "Ensure stock_notifications table exists for back-in-stock notifications.",
            lambda conn: None,  # db.create_all() handles this
        ),

        (
            "2026_06_11_product_reviews_table",
            "Ensure product_reviews table exists for customer ratings.",
            lambda conn: None,  # db.create_all() handles this via ProductReview model
        ),
        (
            "2026_06_11_wishlist_items_table",
            "Ensure wishlist_items table exists for customer save-for-later.",
            lambda conn: None,  # db.create_all() handles this via WishlistItem model
        ),
        (
            "2026_06_11_product_description_notnull",
            "Ensure products.description TEXT column exists.",
            lambda conn: _safe_add_column(conn, "products", "description", "TEXT"),
        ),
        (
            "2026_06_11_product_pack_size",
            "Ensure products.pack_size column exists.",
            lambda conn: _safe_add_column(conn, "products", "pack_size", "VARCHAR(40)"),
        ),
        (
            "2026_06_11_product_slug",
            "Ensure products.slug column exists for SEO URLs.",
            lambda conn: _safe_add_column(conn, "products", "slug", "VARCHAR(160)"),
        ),
        (
            "2026_06_11_product_is_featured",
            "Ensure products.is_featured flag exists.",
            lambda conn: _safe_add_column(conn, "products", "is_featured", "BOOLEAN DEFAULT false"),
        ),
        (
            "2026_06_11_product_slug_index",
            "Add unique index on products.slug for fast slug lookups.",
            lambda conn: _safe_exec(
                conn,
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_products_slug ON products(slug) WHERE slug IS NOT NULL"
            ),
        ),
        (
            "2026_06_11_online_orders_customer_email",
            "Add customer_email column to online_orders for receipt emails.",
            lambda conn: _safe_add_column(conn, "online_orders", "customer_email", "VARCHAR(120)"),
        ),
        (
            "2026_06_11_product_review_indexes",
            "Add performance indexes on product_reviews.",
            lambda conn: (
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_product_reviews_product_id ON product_reviews(product_id)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_product_reviews_phone ON product_reviews(customer_phone)"),
            ),
        ),
        (
            "2026_06_11_wishlist_indexes",
            "Add performance indexes on wishlist_items.",
            lambda conn: (
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_wishlist_phone ON wishlist_items(customer_phone)"),
                _safe_exec(conn, "CREATE UNIQUE INDEX IF NOT EXISTS ix_wishlist_unique ON wishlist_items(customer_phone, product_id)"),
            ),
        ),
        (
            "2026_06_20_user_email_column",
            "Add optional email column to users table (for password reset emails).",
            lambda conn: _safe_add_column(conn, "users", "email", "VARCHAR(120)"),
        ),
        (
            "2026_06_29_customer_credit_limit",
            "Add per-customer credit limit used by credit sale enforcement.",
            lambda conn: _safe_add_column(conn, "customers", "credit_limit", "NUMERIC(12,2) NOT NULL DEFAULT 0"),
        ),
        (
            "2026_06_20_product_perf_indexes",
            "Add product name, is_active, and composite active+qty indexes for store query performance.",
            lambda conn: (
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_product_name ON products(name)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_product_is_active ON products(is_active)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_product_active_qty ON products(is_active, quantity)"),
            ),
        ),
        (
            "2026_06_20_stock_movement_indexes",
            "Add product_id, timestamp, and change_type indexes on stock_movements.",
            lambda conn: (
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_stock_movement_product_id ON stock_movements(product_id)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_stock_movement_timestamp ON stock_movements(timestamp)"),
                _safe_exec(conn, "CREATE INDEX IF NOT EXISTS ix_stock_movement_type ON stock_movements(change_type)"),
            ),
        ),
        (
            "2026_06_21_product_enrichment_columns",
            "Add benefits, origin, storage_tips columns to products "
            "(referenced by store chatbot RAG, product detail page, AI autofill, "
            "price justifier — previously missing, causing AttributeError in store_ai_service).",
            lambda conn: (
                _safe_add_column(conn, "products", "benefits", "TEXT"),
                _safe_add_column(conn, "products", "origin", "VARCHAR(120)"),
                _safe_add_column(conn, "products", "storage_tips", "TEXT"),
            ),
        ),
    ]


def run_pending_migrations(app) -> list[str]:
    # Ensure all model-defined tables exist before running column migrations
    try:
        db.create_all()
    except Exception as exc:
        app.logger.warning("db.create_all() in migrations failed: %s", exc)

    applied_keys = set(
        db.session.execute(db.select(SchemaMigrationRecord.migration_key)).scalars().all()
    )
    applied_now: list[str] = []

    for migration_key, description, migration_fn in _migration_steps():
        if migration_key in applied_keys:
            continue
        try:
            with db.engine.begin() as conn:
                migration_fn(conn)
            db.session.add(
                SchemaMigrationRecord(
                    migration_key=migration_key,
                    description=description,
                )
            )
            db.session.commit()
            applied_now.append(migration_key)
            app.logger.info("Applied schema migration %s", migration_key)
        except Exception as exc:
            db.session.rollback()
            app.logger.exception("Schema migration %s failed: %s", migration_key, exc)
            raise

    # ── Auto-fill product descriptions + images for empty products ─────────
    # Runs after migrations — idempotent, only fills empty fields
    if applied_now:  # Only run on first deploy or when new migrations applied
        try:
            from .product_autofill import autofill_all_empty
            results = autofill_all_empty(limit=200)
            if results.get("updated"):
                app.logger.info(
                    "AI autofill: %d products enriched on startup",
                    results["updated"],
                )
        except Exception as exc:
            app.logger.warning("AI autofill on startup failed (non-fatal): %s", exc)

    return applied_now
