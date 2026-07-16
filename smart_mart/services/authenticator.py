"""Authentication service — login, logout, and password hashing."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from flask_login import login_user, logout_user

from ..extensions import bcrypt, db
from ..models.user import User


def _get_client_ip() -> str:
    from flask import request
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()


def login(username: str, password: str) -> User | None:
    """Verify credentials and create a session."""
    from flask import request as flask_request
    ip = _get_client_ip()

    user: User | None = db.session.execute(
        db.select(User).filter_by(username=username)
    ).scalar_one_or_none()

    if user is None or not check_password(password, user.password_hash):
        # Rate limiting is handled by Flask-Limiter on the login route decorator.
        # Record to LoginAttempt table for audit log.
        try:
            from ..models.login_attempt import LoginAttempt
            db.session.add(LoginAttempt(
                username=username,
                ip_address=ip,
                successful=False,
            ))
            db.session.commit()
        except Exception:
            db.session.rollback()
        return None

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


PASSWORD_MIN_LENGTH = 8


def validate_password_strength(password: str) -> list[str]:
    """Return a list of unmet password requirements (empty = valid)."""
    errors: list[str] = []
    if len(password) < PASSWORD_MIN_LENGTH:
        errors.append(f"At least {PASSWORD_MIN_LENGTH} characters.")
    if not any(c.isupper() for c in password):
        errors.append("At least one uppercase letter.")
    if not any(c.isdigit() for c in password):
        errors.append("At least one digit (0–9).")
    return errors
