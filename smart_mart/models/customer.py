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
    loyalty_points = db.Column(db.Integer, default=0)
    loyalty_tier = db.Column(db.String(20), default="silver")
    total_spent = db.Column(db.Numeric(12, 2), default=0)
    visit_count = db.Column(db.Integer, default=1)
    last_visit = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    @classmethod
    def upsert(cls, name: str, phone: str = None, address: str = None):
        """Create or update a customer record. Caller is responsible for committing.

        Matching priority (prevents merging different people with the same name):
          1. Phone match  — same phone = same person, always update
          2. Name + phone — both match = same person
          3. Name only    — ONLY if the existing record has NO phone
                            (avoids merging "Ram Sharma" with a different "Ram Sharma")
          4. No match     — create new record
        """
        if not name:
            return
        name = name.strip()
        _skip = {"walk-in customer", "walk-in", "walkin", "guest", "customer",
                 "test", "n/a", "na", "none", "unknown", ""}
        if name.lower() in _skip:
            return
        if len(name) < 2 or name.isdigit():
            return

        phone_clean = (phone or "").strip() or None

        try:
            existing = None

            # ── Priority 1: phone match (most precise) ────────────────────
            if phone_clean:
                existing = db.session.execute(
                    db.select(cls).where(cls.phone == phone_clean)
                ).scalar_one_or_none()

            # ── Priority 2: name + phone both match ───────────────────────
            if existing is None and phone_clean:
                existing = db.session.execute(
                    db.select(cls).where(
                        db.func.lower(cls.name) == name.lower(),
                        cls.phone == phone_clean,
                    )
                ).scalar_one_or_none()

            # ── Priority 3: name match ONLY if existing has no phone ──────
            # This prevents merging two different people with the same name
            # when one or both have phone numbers on file.
            if existing is None:
                name_match = db.session.execute(
                    db.select(cls).where(
                        db.func.lower(cls.name) == name.lower()
                    )
                ).scalars().all()
                # Only merge if there is exactly one match AND it has no phone
                # (or the incoming record also has no phone)
                if len(name_match) == 1 and not name_match[0].phone and not phone_clean:
                    existing = name_match[0]
                # If multiple name matches exist, don't merge — create new

            if existing:
                # Update contact details if we have better info
                if phone_clean and not existing.phone:
                    existing.phone = phone_clean
                if address and not existing.address:
                    existing.address = address
                existing.visit_count = (existing.visit_count or 0) + 1
                existing.last_visit = datetime.now(timezone.utc)
                existing.updated_at = datetime.now(timezone.utc)
            else:
                db.session.add(cls(
                    name=name.strip(),
                    phone=phone_clean,
                    address=address or None,
                    visit_count=1,
                    updated_at=datetime.now(timezone.utc),
                ))
        except Exception:
            db.session.rollback()
            raise
