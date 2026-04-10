"""Backup Log model — tracks automated backup history (Feature #8)."""
from datetime import datetime, timezone
from ..extensions import db


class BackupLog(db.Model):
    __tablename__ = "backup_logs"

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    file_size_bytes = db.Column(db.Integer, nullable=True)
    backup_type = db.Column(db.String(20), nullable=False, default="manual")  # manual|auto
    status = db.Column(db.String(20), nullable=False, default="success")      # success|failed
    notes = db.Column(db.String(255), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc))

    creator = db.relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return f"<BackupLog {self.filename} {self.status}>"
