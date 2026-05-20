"""SmartMart feature pack schema changes.

Revision ID: 20260516_feature_pack
Revises: None
Create Date: 2026-05-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260516_feature_pack"
down_revision = None
branch_labels = None
depends_on = None


def _table_exists(bind, table):
    return inspect(bind).has_table(table)


def _column_exists(bind, table, column):
    if not _table_exists(bind, table):
        return False
    return any(col["name"] == column for col in inspect(bind).get_columns(table))


def _add_column(table, column):
    bind = op.get_bind()
    if _table_exists(bind, table) and not _column_exists(bind, table, column.name):
        op.add_column(table, column)


def _drop_column(table, column):
    bind = op.get_bind()
    if _column_exists(bind, table, column):
        op.drop_column(table, column)


def _create_table(name, *columns):
    bind = op.get_bind()
    if not _table_exists(bind, name):
        op.create_table(name, *columns)


def _drop_table(name):
    bind = op.get_bind()
    if _table_exists(bind, name):
        op.drop_table(name)


def upgrade():
    _add_column("products", sa.Column("low_stock_threshold", sa.Integer(), nullable=True, server_default="500"))
    _add_column("sales", sa.Column("payment_method", sa.String(length=20), nullable=False, server_default="cash"))
    _add_column("sales", sa.Column("sale_type", sa.String(length=20), nullable=False, server_default="regular"))
    _add_column("customers", sa.Column("loyalty_points", sa.Integer(), nullable=True, server_default="0"))
    _add_column("customers", sa.Column("loyalty_tier", sa.String(length=20), nullable=True, server_default="silver"))
    _add_column("customers", sa.Column("total_spent", sa.Numeric(12, 2), nullable=True, server_default="0"))
    _add_column("shop_settings", sa.Column("name", sa.String(length=120), nullable=True, server_default="GoldKernel Dry Fruits & Treats"))
    _add_column("shop_settings", sa.Column("owner_name", sa.String(length=120), nullable=True))

    _create_table(
        "login_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=80), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("successful", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    _create_table(
        "bundles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.true()),
        sa.Column("is_seasonal", sa.Boolean(), nullable=True, server_default=sa.false()),
        sa.Column("season_tag", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    _create_table(
        "bundle_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bundle_id", sa.Integer(), sa.ForeignKey("bundles.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 3), nullable=False),
    )
    _create_table(
        "waste_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 3), nullable=False),
        sa.Column("reason", sa.String(length=50), nullable=False),
        sa.Column("cost_value", sa.Numeric(10, 2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("recorded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    _create_table(
        "supplier_price_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("supplier_name", sa.String(length=200), nullable=True),
        sa.Column("cost_price", sa.Numeric(10, 2), nullable=False),
        sa.Column("quantity_kg", sa.Numeric(10, 3), nullable=True),
        sa.Column("invoice_ref", sa.String(length=100), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=True),
        sa.Column("recorded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    _create_table(
        "sales_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("target_type", sa.String(length=10), nullable=True, server_default="daily"),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    _drop_table("sales_targets")
    _drop_table("supplier_price_records")
    _drop_table("waste_records")
    _drop_table("bundle_items")
    _drop_table("bundles")
    _drop_table("login_attempts")
    _drop_column("shop_settings", "owner_name")
    _drop_column("shop_settings", "name")
    _drop_column("customers", "total_spent")
    _drop_column("customers", "loyalty_tier")
    _drop_column("customers", "loyalty_points")
    _drop_column("sales", "sale_type")
    _drop_column("sales", "payment_method")
    _drop_column("products", "low_stock_threshold")
