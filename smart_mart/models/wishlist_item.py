"""Customer wishlist model."""
from __future__ import annotations
from datetime import datetime, timezone
from ..extensions import db


class WishlistItem(db.Model):
    __tablename__ = "wishlist_items"
    __table_args__ = (
        # Primary lookup by CustomerAccount ID (logged-in store customers)
        db.UniqueConstraint("customer_account_id", "product_id", name="uq_wishlist_item_account"),
        # Legacy phone-based lookup (backward-compatible)
        db.UniqueConstraint("customer_phone", "product_id", name="uq_wishlist_item"),
        db.Index("ix_wishlist_customer_phone", "customer_phone"),
        db.Index("ix_wishlist_customer_account_id", "customer_account_id"),
    )

    id                 = db.Column(db.Integer, primary_key=True)
    customer_phone     = db.Column(db.String(50), nullable=False)
    # FK to CustomerAccount — preferred identifier; nullable for backward-compat
    customer_account_id = db.Column(
        db.Integer,
        db.ForeignKey("customer_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    product_id         = db.Column(db.Integer, db.ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    created_at     = db.Column(db.DateTime, nullable=False,
                                default=lambda: datetime.now(timezone.utc))

    product = db.relationship("Product", backref=db.backref("wishlisted_by", lazy="dynamic"))
