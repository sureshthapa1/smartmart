"""Backup service — database backup and restore (Feature #8)."""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone

from ..extensions import db


def _backup_dir() -> str:
    from flask import current_app
    base = os.path.dirname(os.path.dirname(current_app.instance_path))
    d = os.path.join(base, "backups")
    os.makedirs(d, exist_ok=True)
    return d


def create_backup(user_id: int | None = None, backup_type: str = "manual") -> dict:
    """Create a full JSON backup of all database tables. Returns metadata dict."""
    from flask import current_app
    from ..models.backup_log import BackupLog

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"smart_mart_backup_{timestamp}.json"

    try:
        # Collect all table data
        snapshot = {}
        with db.engine.connect() as conn:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            for table_name in inspector.get_table_names():
                rows = conn.execute(text(f"SELECT * FROM {table_name}")).mappings().all()
                snapshot[table_name] = [dict(r) for r in rows]

        # Serialize (handle non-JSON-serializable types)
        def default_serializer(obj):
            if hasattr(obj, "isoformat"):
                return obj.isoformat()
            return str(obj)

        json_data = json.dumps(snapshot, default=default_serializer, indent=2)
        file_size = len(json_data.encode("utf-8"))

        # Save to backups directory
        backup_path = os.path.join(_backup_dir(), filename)
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(json_data)

        # Log it
        log = BackupLog(
            filename=filename,
            file_size_bytes=file_size,
            backup_type=backup_type,
            status="success",
            created_by=user_id,
        )
        db.session.add(log)
        db.session.commit()

        return {
            "success": True,
            "filename": filename,
            "file_size_bytes": file_size,
            "path": backup_path,
            "json_data": json_data,
        }
    except Exception as e:
        try:
            log = BackupLog(
                filename=filename,
                backup_type=backup_type,
                status="failed",
                notes=str(e)[:255],
                created_by=user_id,
            )
            db.session.add(log)
            db.session.commit()
        except Exception:
            pass
        raise


def list_backups() -> list[dict]:
    """List available backup files with metadata."""
    try:
        d = _backup_dir()
        files = []
        for fname in sorted(os.listdir(d), reverse=True):
            if fname.endswith(".json"):
                fpath = os.path.join(d, fname)
                stat = os.stat(fpath)
                files.append({
                    "filename": fname,
                    "size_bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                })
        return files
    except Exception:
        return []


def get_backup_logs(page: int = 1, per_page: int = 30):
    from ..models.backup_log import BackupLog
    stmt = (db.select(BackupLog)
            .order_by(BackupLog.created_at.desc())
            .limit(per_page).offset((page - 1) * per_page))
    return db.session.execute(stmt).scalars().all()


def delete_backup_file(filename: str) -> bool:
    """Delete a backup file from disk."""
    try:
        path = os.path.join(_backup_dir(), filename)
        if os.path.exists(path) and filename.endswith(".json"):
            os.remove(path)
            return True
    except Exception:
        pass
    return False
