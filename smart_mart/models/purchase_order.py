"""Purchase Order model — PO workflow before goods arrive."""
from datetime import datetime, timezone
from ..extensions import db


class PurchaseOrder(db.Model):
    __tablename__ = "purchase_orders"

    STATUS_LABELS = {
        "draft":     ("Draft",     "secondary"),
        "sent":      ("Sent",      "info"),
        "partial":   ("Partial",   "warning"),
        "received":  ("Received",  "success"),
        "cancelled": ("Cancelled", "danger"),
    }

    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(30), nullable=False, unique=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="draft")
    expected_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    sent_at = db.Column(db.DateTime, nullable=True)
    received_at = db.Column(db.DateTime, nullable=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey("purchases.id"), nullable=True)

    supplier = db.relationship("Supplier", backref="purchase_orders")
    creator = db.relationship("User", foreign_keys=[created_by])
    items = db.relationship("PurchaseOrderItem", back_populates="order", cascade="all, delete-orphan")
    purchase = db.relationship("Purchase", backref=db.backref("purchase_order", uselist=False))

    @property
    def total_cost(self):
        return sum(float(i.unit_cost) * i.quantity for i in self.items)

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, ("Unknown", "secondary"))


class PurchaseOrderItem(db.Model):
    __tablename__ = "purchase_order_items"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("purchase_orders.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity_ordered = db.Column(db.Integer, nullable=False)
    quantity_received = db.Column(db.Integer, nullable=False, default=0)
    unit_cost = db.Column(db.Numeric(10, 2), nullable=False)

    order = db.relationship("PurchaseOrder", back_populates="items")
    product = db.relationship("Product")
