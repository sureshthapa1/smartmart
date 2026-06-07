"""E-commerce integration models for website/POS synchronization."""
from __future__ import annotations

from datetime import datetime, timezone

from ..extensions import db


class StockReservation(db.Model):
    __tablename__ = "stock_reservations"
    __table_args__ = (
        db.Index("ix_stock_reservations_product_status", "product_id", "status"),
        db.Index("ix_stock_reservations_order_status", "order_id", "status"),
        db.Index("ix_stock_reservations_expires_at", "expires_at"),
        db.UniqueConstraint("reservation_key", name="uq_stock_reservations_key"),
    )

    id = db.Column(db.Integer, primary_key=True)
    reservation_key = db.Column(db.String(140), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey("online_orders.id"), nullable=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="active")
    source = db.Column(db.String(30), nullable=False, default="website")
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    notes = db.Column(db.Text, nullable=True)

    order = db.relationship("OnlineOrder", backref=db.backref("stock_reservations", lazy="select"))
    product = db.relationship("Product", backref=db.backref("stock_reservations", lazy="select"))


class EcommercePayment(db.Model):
    __tablename__ = "payments"
    __table_args__ = (
        db.Index("ix_payments_order_id", "order_id"),
        db.Index("ix_payments_provider_status", "provider", "status"),
        db.UniqueConstraint("provider", "transaction_id", name="uq_payments_provider_transaction"),
    )

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("online_orders.id"), nullable=False)
    provider = db.Column(db.String(30), nullable=False)
    method = db.Column(db.String(30), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(8), nullable=False, default="NPR")
    status = db.Column(db.String(20), nullable=False, default="pending")
    transaction_id = db.Column(db.String(120), nullable=True)
    gateway_reference = db.Column(db.String(160), nullable=True)
    raw_payload_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    order = db.relationship("OnlineOrder", backref=db.backref("payments", lazy="select"))


class SyncLog(db.Model):
    __tablename__ = "sync_logs"
    __table_args__ = (
        db.Index("ix_sync_logs_direction_status", "direction", "status"),
        db.Index("ix_sync_logs_entity", "entity_type", "entity_id"),
        db.Index("ix_sync_logs_created_at", "created_at"),
        db.UniqueConstraint("idempotency_key", name="uq_sync_logs_idempotency_key"),
    )

    id = db.Column(db.Integer, primary_key=True)
    direction = db.Column(db.String(30), nullable=False)
    entity_type = db.Column(db.String(40), nullable=False)
    entity_id = db.Column(db.String(80), nullable=True)
    action = db.Column(db.String(40), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    idempotency_key = db.Column(db.String(160), nullable=True)
    request_payload = db.Column(db.Text, nullable=True)
    response_payload = db.Column(db.Text, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    attempts = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
