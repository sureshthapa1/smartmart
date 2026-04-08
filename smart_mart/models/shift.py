"""Shift management — per-staff shift tracking with sales totals."""
from datetime import datetime, timezone
from ..extensions import db


class Shift(db.Model):
    __tablename__ = "shifts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    cash_session_id = db.Column(db.Integer, db.ForeignKey("cash_sessions.id"), nullable=True)
    opening_cash = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    closing_cash = db.Column(db.Numeric(10, 2), nullable=True)
    total_sales = db.Column(db.Numeric(10, 2), nullable=True)
    total_transactions = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    started_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    ended_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="open")  # open|closed

    user = db.relationship("User", backref="shifts")
    cash_session = db.relationship("CashSession", backref=db.backref("shift", uselist=False))

    @property
    def duration_minutes(self):
        if self.ended_at and self.started_at:
            return int((self.ended_at - self.started_at).total_seconds() / 60)
        return None
