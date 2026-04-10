"""CustomerRiskScore model — persists computed risk scores and admin overrides."""
from __future__ import annotations

from datetime import datetime, timezone

from ..extensions import db


class CustomerRiskScore(db.Model):
    """Stores the latest computed risk score and optional admin override for each customer."""

    __tablename__ = "customer_risk_scores"

    id = db.Column(db.Integer, primary_key=True)
    customer_name = db.Column(db.String(200), nullable=False, unique=True, index=True)

    # Computed values
    risk_score = db.Column(db.Integer, nullable=False, default=0)
    risk_tier = db.Column(db.String(20), nullable=False, default="safe")  # safe | watch | risky

    # Admin override (nullable — None means no override active)
    override_tier = db.Column(db.String(20), nullable=True)
    override_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    override_at = db.Column(db.DateTime, nullable=True)

    last_computed_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    @property
    def effective_tier(self) -> str:
        """Return override_tier if set, otherwise computed risk_tier."""
        return self.override_tier if self.override_tier else self.risk_tier

    @property
    def has_override(self) -> bool:
        return self.override_tier is not None

    def __repr__(self) -> str:
        return f"<CustomerRiskScore {self.customer_name!r} score={self.risk_score} tier={self.effective_tier}>"
