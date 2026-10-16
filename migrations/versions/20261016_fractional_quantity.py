"""Convert quantity columns to Numeric(10,3) to support fractional units (e.g. 0.1 kg).

Revision ID: 20261016_fractional_quantity
Revises: 20260607_ecommerce
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa


def upgrade():
    # products.quantity: Integer → Numeric(10,3)
    op.alter_column(
        "products", "quantity",
        existing_type=sa.Integer(),
        type_=sa.Numeric(10, 3),
        existing_nullable=False,
        postgresql_using="quantity::numeric",
    )

    # sale_items.quantity: Integer → Numeric(10,3)
    op.alter_column(
        "sale_items", "quantity",
        existing_type=sa.Integer(),
        type_=sa.Numeric(10, 3),
        existing_nullable=False,
        postgresql_using="quantity::numeric",
    )

    # stock_movements.change_amount: Integer → Numeric(10,3)
    op.alter_column(
        "stock_movements", "change_amount",
        existing_type=sa.Integer(),
        type_=sa.Numeric(10, 3),
        existing_nullable=False,
        postgresql_using="change_amount::numeric",
    )

    # sale_return_items.quantity: Integer → Numeric(10,3)
    op.alter_column(
        "sale_return_items", "quantity",
        existing_type=sa.Integer(),
        type_=sa.Numeric(10, 3),
        existing_nullable=False,
        postgresql_using="quantity::numeric",
    )

    # stock_take_items.system_qty / counted_qty: Integer → Numeric(10,3)
    op.alter_column(
        "stock_take_items", "system_qty",
        existing_type=sa.Integer(),
        type_=sa.Numeric(10, 3),
        existing_nullable=False,
        postgresql_using="system_qty::numeric",
    )
    op.alter_column(
        "stock_take_items", "counted_qty",
        existing_type=sa.Integer(),
        type_=sa.Numeric(10, 3),
        existing_nullable=True,
        postgresql_using="counted_qty::numeric",
    )

    # purchase_items.quantity: Integer → Numeric(10,3)
    op.alter_column(
        "purchase_items", "quantity",
        existing_type=sa.Integer(),
        type_=sa.Numeric(10, 3),
        existing_nullable=False,
        postgresql_using="quantity::numeric",
    )

    # supplier_return_items.quantity: Integer → Numeric(10,3)
    op.alter_column(
        "supplier_return_items", "quantity",
        existing_type=sa.Integer(),
        type_=sa.Numeric(10, 3),
        existing_nullable=False,
        postgresql_using="quantity::numeric",
    )


def downgrade():
    for table, col in [
        ("supplier_return_items", "quantity"),
        ("purchase_items", "quantity"),
        ("stock_take_items", "counted_qty"),
        ("stock_take_items", "system_qty"),
        ("sale_return_items", "quantity"),
        ("stock_movements", "change_amount"),
        ("sale_items", "quantity"),
        ("products", "quantity"),
    ]:
        op.alter_column(
            table, col,
            existing_type=sa.Numeric(10, 3),
            type_=sa.Integer(),
            existing_nullable=(col == "counted_qty"),
            postgresql_using=f"{col}::integer",
        )
