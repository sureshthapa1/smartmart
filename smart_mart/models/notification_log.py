"""Notification log model — imported from notification_service to avoid circular imports."""
from datetime import datetime, timezone
from ..extensions import db


class NotificationLog(db.Model):
    __tablename__ = "notification_logs"
    id = db.Column(db.Integer, primary_key=True)
    recipient = db.Column(db.String(50), nullable=False)
    channel = db.Column(db.String(20), nullable=False, default="sms")
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="queued")
    provider_ref = db.Column(db.String(120), nullable=True)
    error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    sent_at = db.Column(db.DateTime, nullable=True)
