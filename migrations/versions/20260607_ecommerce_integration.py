"""E-commerce integration tables.

Revision ID: 20260607_ecommerce
Revises: 20260516_feature_pack
Create Date: 2026-06-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260607_ecommerce"
down_revision = "20260516_feature_pack"
branch_labels = None
depends_on = None


def _table_exists(bind, table):
    return inspect(bind).has_table(table)


def _index_exists(bind, table, index_name):
    if not _table_exists(bind, table):
        return False
    return any(index["name"] == index_name for index in inspect(bind).get_indexes(table))


def _create_table(name, *columns, **kwargs):
    bind = op.get_bind()
    if not _table_exists(bind, name):
        op.create_table(name, *columns, **kwargs)


def _create_index(name, table, columns):
    bind = op.get_bind()
    if _table_exists(bind, table) and not _index_exists(bind, table, name):
        op.create_index(name, table, columns)


def _drop_table(name):
    bind = op.get_bind()
    if _table_exists(bind, name):
        op.drop_table(name)


def upgrade():
    _create_table(
        "stock_reservations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reservation_key", sa.String(length=140), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("online_orders.id"), nullable=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("source", sa.String(length=30), nullable=False, server_default="website"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint("reservation_key", name="uq_stock_reservations_key"),
    )
    _create_index("ix_stock_reservations_product_status", "stock_reservations", ["product_id", "status"])
    _create_index("ix_stock_reservations_order_status", "stock_reservations", ["order_id", "status"])
    _create_index("ix_stock_reservations_expires_at", "stock_reservations", ["expires_at"])

    _create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("online_orders.id"), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("method", sa.String(length=30), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="NPR"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("transaction_id", sa.String(length=120), nullable=True),
        sa.Column("gateway_reference", sa.String(length=160), nullable=True),
        sa.Column("raw_payload_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("provider", "transaction_id", name="uq_payments_provider_transaction"),
    )
    _create_index("ix_payments_order_id", "payments", ["order_id"])
    _create_index("ix_payments_provider_status", "payments", ["provider", "status"])

    _create_table(
        "sync_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("direction", sa.String(length=30), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.String(length=80), nullable=True),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("idempotency_key", sa.String(length=160), nullable=True),
        sa.Column("request_payload", sa.Text(), nullable=True),
        sa.Column("response_payload", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_sync_logs_idempotency_key"),
    )
    _create_index("ix_sync_logs_direction_status", "sync_logs", ["direction", "status"])
    _create_index("ix_sync_logs_entity", "sync_logs", ["entity_type", "entity_id"])
    _create_index("ix_sync_logs_created_at", "sync_logs", ["created_at"])


def downgrade():
    _drop_table("sync_logs")
    _drop_table("payments")
    _drop_table("stock_reservations")
