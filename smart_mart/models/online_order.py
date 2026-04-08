"""Online Order model — tracks orders placed online with delivery status."""

from datetime import datetime, timezone
from ..extensions import db


class OnlineOrder(db.Model):
    __tablename__ = "online_orders"

    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(30), unique=True, nullable=False)

    # Customer info
    customer_name = db.Column(db.String(120), nullable=False)
    customer_phone = db.Column(db.String(50), nullable=False)
    customer_email = db.Column(db.String(120), nullable=True)
    delivery_address = db.Column(db.String(500), nullable=False)
    delivery_area = db.Column(db.String(120), nullable=True)

    # Order details
    total_amount = db.Column(db.Numeric(10, 2), nullable=False)
    delivery_charge = db.Column(db.Numeric(10, 2), default=0)
    discount_amount = db.Column(db.Numeric(10, 2), default=0)
    payment_mode = db.Column(db.String(20), default="cod")  # cod|online|card|qr
    payment_status = db.Column(db.String(20), default="pending")  # pending|paid|failed|refunded

    # Delivery status
    status = db.Column(db.String(30), default="pending")
    # pending → confirmed → preparing → out_for_delivery → delivered | cancelled | returned

    # Tracking
    notes = db.Column(db.Text, nullable=True)
    assigned_to = db.Column(db.String(120), nullable=True)  # delivery person
    estimated_delivery = db.Column(db.DateTime, nullable=True)
    delivered_at = db.Column(db.DateTime, nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    cancel_reason = db.Column(db.String(255), nullable=True)

    # Source
    order_source = db.Column(db.String(30), default="website")  # website|phone|whatsapp|app

    # Timestamps
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # Relationships
    items = db.relationship("OnlineOrderItem", back_populates="order",
                            cascade="all, delete-orphan")
    creator = db.relationship("User", foreign_keys=[created_by])

    STATUS_FLOW = ["pending", "confirmed", "preparing", "out_for_delivery", "delivered"]
    STATUS_LABELS = {
        "pending": ("⏳ Pending", "warning"),
        "confirmed": ("✅ Confirmed", "info"),
        "preparing": ("🔧 Preparing", "primary"),
        "out_for_delivery": ("🚴 Out for Delivery", "success"),
        "delivered": ("📦 Delivered", "success"),
        "cancelled": ("❌ Cancelled", "danger"),
        "returned": ("↩️ Returned", "secondary"),
    }

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, (self.status, "secondary"))

    @property
    def grand_total(self):
        return float(self.total_amount) + float(self.delivery_charge or 0) - float(self.discount_amount or 0)

    def next_status(self):
        try:
            idx = self.STATUS_FLOW.index(self.status)
            return self.STATUS_FLOW[idx + 1] if idx + 1 < len(self.STATUS_FLOW) else None
        except ValueError:
            return None


class OnlineOrderItem(db.Model):
    __tablename__ = "online_order_items"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("online_orders.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=True)
    product_name = db.Column(db.String(120), nullable=False)  # snapshot
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False)

    order = db.relationship("OnlineOrder", back_populates="items")
    product = db.relationship("Product")
