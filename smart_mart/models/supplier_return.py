"""Supplier Return model — return goods back to supplier (Feature #5)."""
from datetime import datetime, timezone
from ..extensions import db


class SupplierReturn(db.Model):
    __tablename__ = "supplier_returns"

    STATUS_LABELS = {
        "pending":   ("Pending",   "warning"),
        "sent":      ("Sent",      "info"),
        "credited":  ("Credited",  "success"),
        "cancelled": ("Cancelled", "danger"),
    }

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(30), nullable=False, unique=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=False)
    purchase_id = db.Column(db.Integer, db.ForeignKey("purchases.id"), nullable=True)
    reason = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="pending")
    credit_amount = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    sent_at = db.Column(db.DateTime, nullable=True)

    supplier = db.relationship("Supplier", backref=db.backref("supplier_returns", lazy="select"))
    purchase = db.relationship("Purchase", backref=db.backref("supplier_returns", lazy="select"))
    creator = db.relationship("User", foreign_keys=[created_by])
    items = db.relationship("SupplierReturnItem", back_populates="supplier_return",
                            cascade="all, delete-orphan")

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, ("Unknown", "secondary"))

    @property
    def total_value(self):
        return sum(float(i.unit_cost) * i.quantity for i in self.items)

    def __repr__(self):
        return f"<SupplierReturn #{self.id} {self.reference}>"


class SupplierReturnItem(db.Model):
    __tablename__ = "supplier_return_items"

    id = db.Column(db.Integer, primary_key=True)
    supplier_return_id = db.Column(db.Integer, db.ForeignKey("supplier_returns.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_cost = db.Column(db.Numeric(10, 2), nullable=False)
    reason = db.Column(db.String(120), nullable=True)

    supplier_return = db.relationship("SupplierReturn", back_populates="items")
    product = db.relationship("Product")

    @property
    def subtotal(self):
        return float(self.unit_cost) * self.quantity

    def __repr__(self):
        return f"<SupplierReturnItem return={self.supplier_return_id} product={self.product_id}>"
