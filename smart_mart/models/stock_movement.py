from datetime import datetime, timezone
from ..extensions import db


class StockMovement(db.Model):
    __tablename__ = "stock_movements"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    change_amount = db.Column(db.Integer, nullable=False)  # positive = in, negative = out
    change_type = db.Column(db.String(20), nullable=False)  # 'sale'|'purchase'|'adjustment_in'|'adjustment_out'
    reference_id = db.Column(db.Integer, nullable=True)
    note = db.Column(db.Text, nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    product = db.relationship("Product", back_populates="stock_movements")
    creator = db.relationship("User", back_populates="stock_movements")

    def __repr__(self):
        return f"<StockMovement product={self.product_id} change={self.change_amount} type={self.change_type}>"
