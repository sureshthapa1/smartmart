"""Product review model for e-commerce store."""
from __future__ import annotations
from datetime import datetime, timezone
from ..extensions import db


class ProductReview(db.Model):
    __tablename__ = "product_reviews"
    __table_args__ = (
        db.Index("ix_product_reviews_product_id", "product_id"),
        db.Index("ix_product_reviews_customer_phone", "customer_phone"),
        db.UniqueConstraint("product_id", "customer_phone", name="uq_review_per_customer"),
    )

    id             = db.Column(db.Integer, primary_key=True)
    product_id     = db.Column(db.Integer, db.ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    customer_phone = db.Column(db.String(50), nullable=False)
    customer_name  = db.Column(db.String(120), nullable=False)
    rating         = db.Column(db.Integer, nullable=False)   # 1-5
    title          = db.Column(db.String(120), nullable=True)
    body           = db.Column(db.Text, nullable=True)
    is_approved    = db.Column(db.Boolean, nullable=False, default=False, index=True)  # requires admin approval
    order_number   = db.Column(db.String(30), nullable=True)  # verify purchase
    created_at     = db.Column(db.DateTime, nullable=False,
                                default=lambda: datetime.now(timezone.utc))

    product = db.relationship("Product", backref=db.backref("reviews", lazy="dynamic"))

    @property
    def star_display(self) -> str:
        return "★" * self.rating + "☆" * (5 - self.rating)
