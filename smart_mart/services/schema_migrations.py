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

    return applied_now
