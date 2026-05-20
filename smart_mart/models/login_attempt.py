from datetime import datetime, timezone

from ..extensions import db


class LoginAttempt(db.Model):
    __tablename__ = "login_attempts"
    __table_args__ = (
        db.Index("ix_login_attempt_username", "username"),
        db.Index("ix_login_attempt_created_at", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    successful = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self):
        status = "success" if self.successful else "failed"
        return f"<LoginAttempt {status} {self.username or '-'} {self.ip_address or '-'}>"
