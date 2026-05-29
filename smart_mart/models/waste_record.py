from datetime import datetime, timezone

from ..extensions import db


class WasteRecord(db.Model):
    __tablename__ = "waste_records"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Numeric(10, 3), nullable=False)
    reason = db.Column(db.String(50), nullable=False)
    cost_value = db.Column(db.Numeric(10, 2))
    notes = db.Column(db.Text)
    recorded_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    product = db.relationship("Product")
    recorder = db.relationship("User")
