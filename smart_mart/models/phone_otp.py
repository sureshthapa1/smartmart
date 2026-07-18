"""OTP model for phone verification — used by store customer registration."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import random
import string
from ..extensions import db


class PhoneOTP(db.Model):
    """One-time passcode for verifying a Nepal phone number at registration."""
    __tablename__ = "phone_otps"

    id         = db.Column(db.Integer, primary_key=True)
    phone      = db.Column(db.String(20), nullable=False, index=True)
    code       = db.Column(db.String(6),  nullable=False)
    created_at = db.Column(db.DateTime(timezone=True),
                           default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used       = db.Column(db.Boolean, default=False)
    attempts   = db.Column(db.Integer, default=0)  # brute-force guard

    @classmethod
    def generate(cls, phone: str, ttl_minutes: int = 10) -> "PhoneOTP":
        """Create a new 6-digit OTP for the given phone number."""
        # Invalidate any previous unused OTPs for this phone
        db.session.execute(
            db.update(cls)
            .where(cls.phone == phone, cls.used == False)
            .values(used=True)
        )
        code = "".join(random.choices(string.digits, k=6))
        otp  = cls(
            phone=phone,
            code=code,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
        )
        db.session.add(otp)
        db.session.commit()
        return otp

    @classmethod
    def verify(cls, phone: str, code: str) -> bool:
        """Return True if the code is valid and mark it used. Increments attempts."""
        otp = db.session.execute(
            db.select(cls)
            .where(
                cls.phone == phone,
                cls.used  == False,
                cls.expires_at > datetime.now(timezone.utc),
            )
            .order_by(cls.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        if otp is None:
            return False

        otp.attempts += 1
        if otp.attempts > 5:  # brute-force guard
            otp.used = True
            db.session.commit()
            return False

        if otp.code == code:
            otp.used = True
            db.session.commit()
            return True

        db.session.commit()
        return False

    def __repr__(self) -> str:
        return f"<PhoneOTP phone={self.phone} expires={self.expires_at}>"
