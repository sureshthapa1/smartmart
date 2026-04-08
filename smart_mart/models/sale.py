from datetime import datetime, timezone
from ..extensions import db


class Sale(db.Model):
    __tablename__ = "sales"
    __table_args__ = (
        db.Index("ix_sale_date", "sale_date"),
        db.Index("ix_sale_payment_mode", "payment_mode"),
        db.Index("ix_sale_customer_name", "customer_name"),
    )

    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(30), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    total_amount = db.Column(db.Numeric(10, 2), nullable=False)
    sale_date = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    customer_name = db.Column(db.String(120), nullable=True)
    customer_address = db.Column(db.String(255), nullable=True)
    customer_phone = db.Column(db.String(50), nullable=True)
    payment_mode = db.Column(db.String(20), nullable=True, default="cash")  # cash|qr|card|other
    discount_amount = db.Column(db.Numeric(10, 2), nullable=True, default=0)
    discount_note = db.Column(db.String(120), nullable=True)
    credit_due_date = db.Column(db.Date, nullable=True)   # collection reminder for credit/udharo
    credit_collected = db.Column(db.Boolean, nullable=False, default=False)

    # Relationships
    user = db.relationship("User", back_populates="sales")
    items = db.relationship("SaleItem", back_populates="sale", cascade="all, delete-orphan")
    returns = db.relationship("SaleReturn", back_populates="sale", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Sale #{self.id} total={self.total_amount}>"


class SaleItem(db.Model):
    __tablename__ = "sale_items"
    __table_args__ = (
        db.Index("ix_sale_item_sale_id", "sale_id"),
        db.Index("ix_sale_item_product_id", "product_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False)

    # Relationships
    sale = db.relationship("Sale", back_populates="items")
    product = db.relationship("Product", back_populates="sale_items")
    return_items = db.relationship(
        "SaleReturnItem", back_populates="sale_item", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<SaleItem sale={self.sale_id} product={self.product_id} qty={self.quantity}>"
