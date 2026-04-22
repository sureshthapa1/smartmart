"""Financial period close/lock model."""
from __future__ import annotations

from datetime import datetime, timezone

from ..extensions import db


class FinancialPeriod(db.Model):
    __tablename__ = "financial_periods"
    __table_args__ = (
        db.UniqueConstraint("year", "month", name="uq_financial_period_ym"),
    )

    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)          # 1-12
    status = db.Column(db.String(20), nullable=False, default="open")  # open|closed|locked
    closed_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    closed_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    # Snapshot totals captured at close time
    total_sales = db.Column(db.Numeric(14, 2), nullable=True)
    total_cogs = db.Column(db.Numeric(14, 2), nullable=True)
    total_opex = db.Column(db.Numeric(14, 2), nullable=True)
    net_profit = db.Column(db.Numeric(14, 2), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    closer = db.relationship("User", foreign_keys=[closed_by])

    @property
    def label(self) -> str:
        import calendar
        return f"{calendar.month_name[self.month]} {self.year}"

    @classmethod
    def get_or_create(cls, year: int, month: int) -> "FinancialPeriod":
        period = db.session.execute(
            db.select(cls).where(cls.year == year, cls.month == month)
        ).scalar_one_or_none()
        if not period:
            period = cls(year=year, month=month)
            db.session.add(period)
            db.session.flush()
        return period

    @classmethod
    def is_locked(cls, year: int, month: int) -> bool:
        period = db.session.execute(
            db.select(cls).where(cls.year == year, cls.month == month)
        ).scalar_one_or_none()
        return period is not None and period.status in ("closed", "locked")
