"""Audit Log model — field-level change tracking (Feature #9)."""
from datetime import datetime, timezone
from ..extensions import db


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    __table_args__ = (
        db.Index("ix_audit_log_user_id", "user_id"),
        db.Index("ix_audit_log_entity", "entity_type", "entity_id"),
        db.Index("ix_audit_log_created_at", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    username = db.Column(db.String(80), nullable=True)   # denormalized for history
    action = db.Column(db.String(20), nullable=False)    # create|update|delete
    entity_type = db.Column(db.String(50), nullable=False)  # Product|Sale|User|...
    entity_id = db.Column(db.Integer, nullable=True)
    entity_label = db.Column(db.String(120), nullable=True)  # human-readable name
    changes = db.Column(db.Text, nullable=True)          # JSON: {field: [old, new]}
    ip_address = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc))

    user = db.relationship("User", foreign_keys=[user_id],
                           backref=db.backref("audit_logs", lazy="select"))

    def __repr__(self):
        return f"<AuditLog {self.action} {self.entity_type}#{self.entity_id} by {self.username}>"
