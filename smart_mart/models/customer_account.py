"""Customer account model — separate from staff User accounts."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone

from ..extensions import db


class CustomerAccount(db.Model):
    __tablename__ = "customer_accounts"
    __table_args__ = (
        db.Index("ix_customer_accounts_phone", "phone"),
        db.Index("ix_customer_accounts_email", "email"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    address = db.Column(db.String(255), nullable=True)
    # Saved delivery address — auto-filled at checkout for repeat customers
    saved_address   = db.Column(db.Text,    nullable=True)
    saved_area      = db.Column(db.String(100), nullable=True)
    save_address_pref = db.Column(db.Boolean, default=False)
    area = db.Column(db.String(120), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc))
    last_login = db.Column(db.DateTime, nullable=True)
    notes       = db.Column(db.Text, nullable=True)  # Staff notes: preferences, allergies, special requests

    # ── helpers ──────────────────────────────────────────────────────────────

    def set_password(self, password: str) -> None:
        from ..extensions import bcrypt
        self.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")

    def check_password(self, password: str) -> bool:
        from ..extensions import bcrypt
        try:
            return bcrypt.check_password_hash(self.password_hash, password)
        except Exception:
            return False

    def touch_login(self) -> None:
        self.last_login = datetime.now(timezone.utc)

    def __repr__(self) -> str:
        return f"<CustomerAccount {self.phone}>"
