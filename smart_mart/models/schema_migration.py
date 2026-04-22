from datetime import datetime, timezone

from ..extensions import db


class SchemaMigrationRecord(db.Model):
    __tablename__ = "schema_migration_records"

    id = db.Column(db.Integer, primary_key=True)
    migration_key = db.Column(db.String(120), nullable=False, unique=True)
    description = db.Column(db.String(255), nullable=False)
    applied_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
