from datetime import datetime, timezone

from ..extensions import db


class SaleReturn(db.Model):
    __tablename__ = "sale_returns"

    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False)
    processed_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    refund_amount = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    refund_mode = db.Column(db.String(20), nullable=False, default="cash")
    reason = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    sale = db.relationship("Sale", back_populates="returns")
    processor = db.relationship("User")
    items = db.relationship(
        "SaleReturnItem", back_populates="sale_return", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<SaleReturn sale={self.sale_id} refund={self.refund_amount}>"


class SaleReturnItem(db.Model):
    __tablename__ = "sale_return_items"

    id = db.Column(db.Integer, primary_key=True)
    sale_return_id = db.Column(db.Integer, db.ForeignKey("sale_returns.id"), nullable=False)
    sale_item_id = db.Column(db.Integer, db.ForeignKey("sale_items.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False)

    sale_return = db.relationship("SaleReturn", back_populates="items")
    sale_item = db.relationship("SaleItem", back_populates="return_items")
    product = db.relationship("Product")

    def __repr__(self):
        return (
            f"<SaleReturnItem return={self.sale_return_id} "
            f"sale_item={self.sale_item_id} qty={self.quantity}>"
        )
