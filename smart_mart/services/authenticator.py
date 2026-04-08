"""Authentication service — login, logout, and password hashing."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone, timedelta
from threading import Lock

from flask_login import login_user, logout_user

from ..extensions import bcrypt, db
from ..models.user import User

# ── Simple in-memory rate limiter (no external deps) ─────────────────────────
_login_attempts: dict[str, list[datetime]] = defaultdict(list)
_lock = Lock()
_MAX_ATTEMPTS = 5
_WINDOW_MINUTES = 10


def _get_client_ip() -> str:
    from flask import request
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()


def is_rate_limited(ip: str) -> bool:
    """Return True if this IP has exceeded the login attempt limit."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=_WINDOW_MINUTES)
    with _lock:
        _login_attempts[ip] = [t for t in _login_attempts[ip] if t > cutoff]
        return len(_login_attempts[ip]) >= _MAX_ATTEMPTS


def record_failed_attempt(ip: str) -> int:
    """Record a failed login attempt and return remaining attempts."""
    with _lock:
        _login_attempts[ip].append(datetime.now(timezone.utc))
        return max(0, _MAX_ATTEMPTS - len(_login_attempts[ip]))


def clear_attempts(ip: str) -> None:
    with _lock:
        _login_attempts.pop(ip, None)


def login(username: str, password: str) -> User | None:
    """Verify credentials and create a session."""
    from flask import request as flask_request
    ip = _get_client_ip()

    if is_rate_limited(ip):
        return None

    user: User | None = db.session.execute(
        db.select(User).filter_by(username=username)
    ).scalar_one_or_none()

    if user is None or not check_password(password, user.password_hash):
        record_failed_attempt(ip)
        return None

    clear_attempts(ip)
    login_user(user)

    try:
        from ..models.user_activity import UserActivity
        session = UserActivity(
            user_id=user.id,
            ip_address=flask_request.remote_addr,
        )
        db.session.add(session)
        db.session.commit()
        from flask import session as flask_session
        flask_session["activity_id"] = session.id
    except Exception:
        pass

    return user


def logout() -> None:
    """Invalidate the current session."""
    try:
        from flask import session as flask_session
        from ..models.user_activity import UserActivity
        activity_id = flask_session.get("activity_id")
        if activity_id:
            act = db.session.get(UserActivity, activity_id)
            if act and act.logout_at is None:
                act.logout_at = datetime.now(timezone.utc)
                db.session.commit()
    except Exception:
        pass
    logout_user()


def hash_password(plaintext: str) -> str:
    return bcrypt.generate_password_hash(plaintext).decode("utf-8")


def check_password(plaintext: str, hashed: str) -> bool:
    return bcrypt.check_password_hash(hashed, plaintext)
