"""Idempotency key store — prevents double-posting of sales."""
from __future__ import annotations

from datetime import datetime, timezone

from ..extensions import db


class IdempotencyKey(db.Model):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        db.Index("ix_idempotency_key_user", "user_id", "key"),
    )

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(128), nullable=False, unique=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    endpoint = db.Column(db.String(80), nullable=False)
    result_sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    @classmethod
    def consume(cls, key: str, user_id: int, endpoint: str) -> "IdempotencyKey | None":
        """Return existing record if key already used, else create a placeholder.

        Uses a check-then-insert with IntegrityError catch to handle the
        TOCTOU race where two concurrent POSTs with the same key both pass
        the initial SELECT. The unique constraint on `key` guarantees only one
        succeeds; the loser re-queries and returns the winner's row so the
        caller can detect the duplicate cleanly instead of seeing a 500.
        """
        from sqlalchemy.exc import IntegrityError

        existing = db.session.execute(
            db.select(cls).where(cls.key == key)
        ).scalar_one_or_none()
        if existing:
            return existing
        record = cls(key=key, user_id=user_id, endpoint=endpoint)
        db.session.add(record)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            # The race loser: the winner's row is now committed — re-fetch it.
            existing = db.session.execute(
                db.select(cls).where(cls.key == key)
            ).scalar_one_or_none()
            return existing  # non-None signals "duplicate — do not proceed"
        return None  # None means "first time — proceed"
