from __future__ import annotations

from datetime import datetime, timezone

from ...extensions import db


class PurchaseBatch(db.Model):
    __tablename__ = "bi_purchase_batches"

    id = db.Column(db.Integer, primary_key=True)
    supplier_name = db.Column(db.String(120), nullable=True)
    purchase_date = db.Column(db.Date, nullable=False)
    allocation_method = db.Column(db.String(20), nullable=False, default="value")
    status = db.Column(db.String(20), nullable=False, default="draft")
    subtotal_amount = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    shared_expense_total = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    grand_total = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    allocation_snapshot = db.Column(db.JSON, nullable=True)
    finalized_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    items = db.relationship("PurchaseBatchItem", back_populates="batch", cascade="all, delete-orphan")
    expenses = db.relationship("PurchaseBatchExpense", back_populates="batch", cascade="all, delete-orphan")


class PurchaseBatchItem(db.Model):
    __tablename__ = "bi_purchase_batch_items"

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("bi_purchase_batches.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False)
    purchase_price = db.Column(db.Numeric(14, 4), nullable=False)
    allocated_total = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    allocated_cost_per_unit = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    final_cost = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    allocation_detail = db.Column(db.JSON, nullable=True)
    # Task 8: lot/batch tracking
    lot_number = db.Column(db.String(80), nullable=True)
    batch_expiry = db.Column(db.Date, nullable=True)

    batch = db.relationship("PurchaseBatch", back_populates="items")
    product = db.relationship("Product")


class PurchaseBatchExpense(db.Model):
    __tablename__ = "bi_purchase_batch_expenses"

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("bi_purchase_batches.id"), nullable=False, index=True)
    expense_type = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)

    batch = db.relationship("PurchaseBatch", back_populates="expenses")
