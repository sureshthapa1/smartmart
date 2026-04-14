"""Recurring Expense model — tracks regular bills like rent, electricity, tax."""
from datetime import datetime, timezone
from ..extensions import db


class RecurringExpense(db.Model):
    __tablename__ = "recurring_expenses"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)          # e.g. "Shop Rent"
    expense_type = db.Column(db.String(50), nullable=False)   # rent|utilities|salary|tax|other
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    frequency = db.Column(db.String(20), nullable=False, default="monthly")
    # monthly | quarterly | yearly | weekly | custom
    frequency_days = db.Column(db.Integer, nullable=True)     # for custom frequency
    next_due_date = db.Column(db.Date, nullable=False)
    reminder_days = db.Column(db.Integer, nullable=False, default=7)  # remind N days before
    notes = db.Column(db.String(255), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    last_paid_at = db.Column(db.Date, nullable=True)

    creator = db.relationship("User", foreign_keys=[created_by])

    @property
    def frequency_label(self):
        labels = {
            "weekly": "Weekly", "monthly": "Monthly",
            "quarterly": "Every 3 Months", "yearly": "Yearly",
            "custom": f"Every {self.frequency_days} days",
        }
        return labels.get(self.frequency, self.frequency.capitalize())

    def next_due_after_payment(self):
        """Calculate next due date after marking as paid."""
        from datetime import timedelta
        from dateutil.relativedelta import relativedelta
        base = self.next_due_date
        if self.frequency == "weekly":
            return base + timedelta(weeks=1)
        elif self.frequency == "monthly":
            return base + relativedelta(months=1)
        elif self.frequency == "quarterly":
            return base + relativedelta(months=3)
        elif self.frequency == "yearly":
            return base + relativedelta(years=1)
        elif self.frequency == "custom" and self.frequency_days:
            return base + timedelta(days=self.frequency_days)
        from datetime import timedelta
        return base + timedelta(days=30)
