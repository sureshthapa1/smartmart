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
    ]


def run_pending_migrations(app) -> list[str]:
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
