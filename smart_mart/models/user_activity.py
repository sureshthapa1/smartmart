"""User activity / session tracking."""
from datetime import datetime, timezone
from ..extensions import db


class UserActivity(db.Model):
    __tablename__ = "user_activity"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    login_at = db.Column(db.DateTime, nullable=False,
                         default=lambda: datetime.now(timezone.utc))
    logout_at = db.Column(db.DateTime, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    # page_views tracked separately
    page_views = db.Column(db.Integer, default=0)

    user = db.relationship("User", backref=db.backref("activity_sessions", lazy="select"))

    @property
    def duration_minutes(self) -> int:
        end = self.logout_at or datetime.now(timezone.utc)
        if self.login_at.tzinfo is None:
            start = self.login_at.replace(tzinfo=timezone.utc)
        else:
            start = self.login_at
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return max(0, int((end - start).total_seconds() / 60))

    @property
    def is_active(self) -> bool:
        return self.logout_at is None
