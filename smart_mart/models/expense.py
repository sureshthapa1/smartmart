from datetime import datetime, timezone
from ..extensions import db


class Expense(db.Model):
    __tablename__ = "expenses"

    id = db.Column(db.Integer, primary_key=True)
    expense_type = db.Column(db.String(20), nullable=False)  # 'rent'|'salary'|'purchase'|'miscellaneous'
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    expense_date = db.Column(db.Date, nullable=False)
    note = db.Column(db.Text, nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    # Mirror row in bi_operating_expenses (kept in sync automatically)
    bi_opex_id = db.Column(db.Integer, db.ForeignKey("bi_operating_expenses.id"), nullable=True)

    # Relationships
    creator = db.relationship("User", back_populates="expenses")

    def __repr__(self):
        return f"<Expense {self.expense_type} amount={self.amount} date={self.expense_date}>"
