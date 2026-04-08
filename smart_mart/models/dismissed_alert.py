from datetime import datetime, timezone
from ..extensions import db


class DismissedAlert(db.Model):
    __tablename__ = "dismissed_alerts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    # alert_key = "<type>:<source_id>"  e.g. "low_stock:42", "expiry:7"
    alert_key = db.Column(db.String(80), nullable=False)
    dismissed_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint("user_id", "alert_key", name="uq_user_alert"),)
