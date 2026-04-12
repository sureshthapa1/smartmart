"""Customer record for autofill on repeat visits."""
from datetime import datetime, timezone
from ..extensions import db


class Customer(db.Model):
    __tablename__ = "customers"
    __table_args__ = (
        db.Index("ix_customer_name", "name"),
        db.Index("ix_customer_phone", "phone"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(50), nullable=True)
    address = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    birthday = db.Column(db.Date, nullable=True)
    visit_count = db.Column(db.Integer, default=1)
    last_visit = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    @classmethod
    def upsert(cls, name: str, phone: str = None, address: str = None):
        """Create or update a customer record. Caller is responsible for committing."""
        if not name or name.strip().lower() in ("walk-in customer", ""):
            return
        try:
            existing = db.session.execute(
                db.select(cls).filter(db.func.lower(cls.name) == name.strip().lower())
            ).scalar_one_or_none()
            if existing:
                if phone:
                    existing.phone = phone
                if address:
                    existing.address = address
                # Increment visit count on every sale (each sale = one visit)
                existing.visit_count = (existing.visit_count or 0) + 1
                existing.last_visit = datetime.now(timezone.utc)
            else:
                db.session.add(cls(
                    name=name.strip(),
                    phone=phone or None,
                    address=address or None,
                    visit_count=1,
                ))
        except Exception:
            db.session.rollback()
            raise
