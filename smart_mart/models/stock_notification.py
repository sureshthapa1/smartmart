"""Stock notification — customer wants to be notified when product is back."""
from datetime import datetime, timezone
from ..extensions import db


class StockNotification(db.Model):
    __tablename__ = "stock_notifications"
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    phone = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    name = db.Column(db.String(120), nullable=True)
    notified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    product = db.relationship("Product")
