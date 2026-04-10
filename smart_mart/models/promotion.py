"""Promotional Pricing model — time-based discounts and bundle deals (Feature #6)."""
from datetime import datetime, timezone
from ..extensions import db


class Promotion(db.Model):
    __tablename__ = "promotions"

    PROMO_TYPES = {
        "percentage": "Percentage Discount (%)",
        "fixed":      "Fixed Amount Off (NPR)",
        "bogo":       "Buy X Get Y Free",
        "bundle":     "Bundle Price",
    }

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    promo_type = db.Column(db.String(20), nullable=False, default="percentage")
    # For percentage/fixed: discount value. For bogo: buy_qty. For bundle: bundle_price.
    discount_value = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    # For BOGO: how many to buy to get free_qty free
    buy_qty = db.Column(db.Integer, nullable=True)
    free_qty = db.Column(db.Integer, nullable=True)
    # Scope: 'all', 'category', 'product'
    scope = db.Column(db.String(20), nullable=False, default="all")
    scope_value = db.Column(db.String(120), nullable=True)  # category name or product id
    min_purchase = db.Column(db.Numeric(10, 2), nullable=True)  # minimum cart value
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    creator = db.relationship("User", foreign_keys=[created_by])

    @property
    def is_currently_active(self):
        from datetime import date
        today = date.today()
        return self.is_active and self.start_date <= today <= self.end_date

    @property
    def type_label(self):
        return self.PROMO_TYPES.get(self.promo_type, self.promo_type)

    def calculate_discount(self, subtotal: float, product=None) -> float:
        """Return the discount amount for a given subtotal."""
        if not self.is_currently_active:
            return 0.0
        if self.min_purchase and subtotal < float(self.min_purchase):
            return 0.0
        if self.promo_type == "percentage":
            return round(subtotal * float(self.discount_value) / 100, 2)
        elif self.promo_type == "fixed":
            return min(float(self.discount_value), subtotal)
        elif self.promo_type == "bundle":
            return max(0.0, subtotal - float(self.discount_value))
        return 0.0

    def __repr__(self):
        return f"<Promotion #{self.id} {self.name} {self.promo_type}>"
