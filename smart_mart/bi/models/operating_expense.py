from __future__ import annotations

from datetime import date, datetime, timezone

from ...extensions import db


class OperatingExpense(db.Model):
    __tablename__ = "bi_operating_expenses"

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(80), nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    expense_date = db.Column(db.Date, nullable=False, default=date.today)
    payment_method = db.Column(db.String(30), nullable=False)
    note = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
