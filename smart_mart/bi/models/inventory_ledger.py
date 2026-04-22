from __future__ import annotations

from datetime import datetime, timezone

from ...extensions import db


class InventoryLedgerEntry(db.Model):
    __tablename__ = "bi_inventory_ledger"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    movement_type = db.Column(db.String(30), nullable=False)
    qty = db.Column(db.Integer, nullable=False)
    unit_cost = db.Column(db.Numeric(14, 4), nullable=False)
    reference_type = db.Column(db.String(30), nullable=False)
    reference_id = db.Column(db.Integer, nullable=False)
    movement_date = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)

    product = db.relationship("Product")
