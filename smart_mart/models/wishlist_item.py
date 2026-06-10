"""Customer wishlist model."""
from __future__ import annotations
from datetime import datetime, timezone
from ..extensions import db


class WishlistItem(db.Model):
    __tablename__ = "wishlist_items"
    __table_args__ = (
        db.UniqueConstraint("customer_phone", "product_id", name="uq_wishlist_item"),
        db.Index("ix_wishlist_customer_phone", "customer_phone"),
    )

    id             = db.Column(db.Integer, primary_key=True)
    customer_phone = db.Column(db.String(50), nullable=False)
    product_id     = db.Column(db.Integer, db.ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    created_at     = db.Column(db.DateTime, nullable=False,
                                default=lambda: datetime.now(timezone.utc))

    product = db.relationship("Product", backref=db.backref("wishlisted_by", lazy="dynamic"))
