from datetime import datetime, timezone

from ..extensions import db


class SalesTarget(db.Model):
    __tablename__ = "sales_targets"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    target_date = db.Column(db.Date, nullable=False)
    target_type = db.Column(db.String(10), default="daily")
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship("User")
