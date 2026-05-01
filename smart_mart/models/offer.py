"""Customer Retention Offer models.

Tables:
  offers            — offer templates (percentage, fixed, conditional, product_based, combo)
  customer_offers   — per-customer offer assignments with expiry and usage tracking
  offer_notifications — delivery log for offer WhatsApp/SMS messages
"""
from __future__ import annotations
from datetime import datetime, timezone, date as _date, timedelta
from ..extensions import db


class Offer(db.Model):
    __tablename__ = "offers"

    OFFER_TYPES = {
        "percentage":    "Percentage Discount (%)",
        "fixed":         "Fixed Amount Off (NPR)",
        "conditional":   "Conditional (min purchase)",
        "product_based": "Product-Based Discount",
        "combo":         "Combo / Bundle Deal",
    }

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    offer_type = db.Column(db.String(20), nullable=False, default="percentage")
    discount_value = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    min_purchase_amount = db.Column(db.Numeric(10, 2), nullable=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    usage_limit = db.Column(db.Integer, nullable=False, default=1)
    valid_days = db.Column(db.Integer, nullable=False, default=30)
    # Optional scheduling: offer only active between start_date and end_date
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    status = db.Column(db.String(20), nullable=False, default="active")  # active | inactive

    # Relationships
    creator = db.relationship("User", foreign_keys=[created_by])
    product = db.relationship("Product", foreign_keys=[product_id])
    customer_offers = db.relationship("CustomerOffer", back_populates="offer", cascade="all, delete-orphan")

    @property
    def type_label(self) -> str:
        return self.OFFER_TYPES.get(self.offer_type, self.offer_type)

    @property
    def is_active(self) -> bool:
        if self.status != "active":
            return False
        today = _date.today()
        if self.start_date and today < self.start_date:
            return False
        if self.end_date and today > self.end_date:
            return False
        return True

    def calculate_discount(self, cart_total: float, product_subtotal: float = 0.0) -> float:
        """Return the discount amount for a given cart total."""
        if not self.is_active:
            return 0.0
        min_amt = float(self.min_purchase_amount or 0)
        if min_amt and cart_total < min_amt:
            return 0.0
        val = float(self.discount_value)
        if self.offer_type == "percentage":
            return round(cart_total * val / 100, 2)
        elif self.offer_type == "fixed":
            return min(val, cart_total)
        elif self.offer_type == "conditional":
            # Only applies when min_purchase_amount is met (already checked above)
            return round(cart_total * val / 100, 2) if val <= 100 else min(val, cart_total)
        elif self.offer_type == "product_based":
            # Discount on the specific product subtotal
            return round(product_subtotal * val / 100, 2) if val <= 100 else min(val, product_subtotal)
        elif self.offer_type == "combo":
            return min(val, cart_total)
        return 0.0

    def __repr__(self) -> str:
        return f"<Offer #{self.id} {self.title} {self.offer_type}>"


class CustomerOffer(db.Model):
    __tablename__ = "customer_offers"
    __table_args__ = (
        db.Index("ix_co_customer_id", "customer_id"),
        db.Index("ix_co_offer_id", "offer_id"),
        db.Index("ix_co_status", "status"),
        db.Index("ix_co_expiry", "expiry_date"),
    )

    STATUS_UNUSED = "unused"
    STATUS_USED = "used"
    STATUS_EXPIRED = "expired"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    offer_id = db.Column(db.Integer, db.ForeignKey("offers.id"), nullable=False)
    assigned_date = db.Column(db.Date, nullable=False, default=_date.today)
    expiry_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="unused")  # unused | used | expired
    usage_count = db.Column(db.Integer, nullable=False, default=0)
    # FK to the sale where this offer was applied (for rollback)
    applied_sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=True)
    # FK to the sale where this offer was assigned (for context)
    assigned_at_sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    customer = db.relationship("Customer", foreign_keys=[customer_id])
    offer = db.relationship("Offer", back_populates="customer_offers")
    applied_sale = db.relationship("Sale", foreign_keys=[applied_sale_id])
    assigned_sale = db.relationship("Sale", foreign_keys=[assigned_at_sale_id])

    @classmethod
    def create_for_customer(
        cls,
        customer_id: int,
        offer_id: int,
        assigned_at_sale_id: int | None = None,
    ) -> "CustomerOffer":
        """Create a new customer offer assignment. Caller must commit."""
        offer = db.session.get(Offer, offer_id)
        if not offer:
            raise ValueError(f"Offer #{offer_id} not found")
        today = _date.today()
        co = cls(
            customer_id=customer_id,
            offer_id=offer_id,
            assigned_date=today,
            expiry_date=today + timedelta(days=offer.valid_days),
            status=cls.STATUS_UNUSED,
            usage_count=0,
            assigned_at_sale_id=assigned_at_sale_id,
        )
        db.session.add(co)
        return co

    @property
    def is_valid(self) -> bool:
        """True if offer is unused and not expired."""
        return self.status == self.STATUS_UNUSED and self.expiry_date >= _date.today()

    @property
    def days_until_expiry(self) -> int:
        return (self.expiry_date - _date.today()).days

    def __repr__(self) -> str:
        return f"<CustomerOffer #{self.id} customer={self.customer_id} offer={self.offer_id} {self.status}>"


class OfferNotification(db.Model):
    """Tracks WhatsApp/SMS notifications sent for offers."""
    __tablename__ = "offer_notifications"

    id = db.Column(db.Integer, primary_key=True)
    customer_offer_id = db.Column(db.Integer, db.ForeignKey("customer_offers.id"), nullable=False)
    notification_type = db.Column(db.String(30), nullable=False)  # assigned | reminder_2d | reminder_1d | expiry
    channel = db.Column(db.String(20), nullable=False, default="sms")  # sms | whatsapp
    status = db.Column(db.String(20), nullable=False, default="queued")  # queued | sent | delivered | failed
    provider_ref = db.Column(db.String(120), nullable=True)
    error = db.Column(db.Text, nullable=True)
    retry_count = db.Column(db.Integer, nullable=False, default=0)
    sent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    customer_offer = db.relationship("CustomerOffer", foreign_keys=[customer_offer_id])

    def __repr__(self) -> str:
        return f"<OfferNotification #{self.id} type={self.notification_type} status={self.status}>"
