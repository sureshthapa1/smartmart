from datetime import datetime, timezone
from ..extensions import db


class Purchase(db.Model):
    __tablename__ = "purchases"

    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=False)
    purchase_date = db.Column(db.Date, nullable=False)
    total_cost = db.Column(db.Numeric(10, 2), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    supplier = db.relationship("Supplier", back_populates="purchases")
    creator = db.relationship("User", back_populates="purchases", foreign_keys=[created_by])
    items = db.relationship("PurchaseItem", back_populates="purchase", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Purchase #{self.id} supplier={self.supplier_id} total={self.total_cost}>"


class PurchaseItem(db.Model):
    __tablename__ = "purchase_items"

    id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey("purchases.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_cost = db.Column(db.Numeric(10, 2), nullable=False)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False)

    # Relationships
    purchase = db.relationship("Purchase", back_populates="items")
    product = db.relationship("Product", back_populates="purchase_items")

    def __repr__(self):
        return f"<PurchaseItem purchase={self.purchase_id} product={self.product_id} qty={self.quantity}>"
