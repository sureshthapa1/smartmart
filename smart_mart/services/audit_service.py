"""Audit service — records who changed what and when (Feature #9)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from flask import request as flask_request
from flask_login import current_user

from ..extensions import db
from ..models.audit_log import AuditLog


def _get_ip() -> str:
    try:
        return flask_request.headers.get("X-Forwarded-For", flask_request.remote_addr or "")
    except Exception:
        return ""


def _get_user_info():
    try:
        if current_user and current_user.is_authenticated:
            return current_user.id, current_user.username
    except Exception:
        pass
    return None, "system"


def log(action: str, entity_type: str, entity_id: int | None = None,
        entity_label: str | None = None, changes: dict | None = None,
        user_id: int | None = None, username: str | None = None) -> None:
    """Write an audit log entry. Safe to call — never raises."""
    try:
        uid, uname = _get_user_info()
        entry = AuditLog(
            user_id=user_id or uid,
            username=username or uname,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_label=entity_label,
            changes=json.dumps(changes) if changes else None,
            ip_address=_get_ip(),
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(entry)
        # Don't commit here — caller controls the transaction
    except Exception:
        pass  # Audit must never break the main flow


def diff(old_obj, new_data: dict, fields: list[str]) -> dict:
    """Build a changes dict comparing old object fields to new_data values."""
    changes = {}
    for field in fields:
        old_val = getattr(old_obj, field, None)
        new_val = new_data.get(field)
        if new_val is not None and str(old_val) != str(new_val):
            changes[field] = [str(old_val), str(new_val)]
    return changes


def get_logs(entity_type: str | None = None, entity_id: int | None = None,
             user_id: int | None = None, page: int = 1, per_page: int = 50) -> list[AuditLog]:
    stmt = db.select(AuditLog).order_by(AuditLog.created_at.desc())
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    stmt = stmt.limit(per_page).offset((page - 1) * per_page)
    return db.session.execute(stmt).scalars().all()


def count_logs(entity_type: str | None = None, user_id: int | None = None) -> int:
    stmt = db.select(db.func.count(AuditLog.id))
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    return db.session.execute(stmt).scalar() or 0
