from __future__ import annotations

from datetime import datetime, timezone

from ...extensions import db


class CategoryMarginRule(db.Model):
    __tablename__ = "bi_category_margin_rules"

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(80), nullable=False, unique=True, index=True)
    margin_pct = db.Column(db.Numeric(8, 4), nullable=False)
    rounding_base = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
