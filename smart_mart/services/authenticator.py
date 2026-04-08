"""Authentication service — login, logout, and password hashing."""

from __future__ import annotations

from flask_login import login_user, logout_user

from ..extensions import bcrypt, db
from ..models.user import User


def login(username: str, password: str) -> User | None:
    """Verify credentials and create a session."""
    from flask import request as flask_request
    user: User | None = db.session.execute(
        db.select(User).filter_by(username=username)
    ).scalar_one_or_none()

    if user is None:
        return None

    if not check_password(password, user.password_hash):
        return None

    login_user(user)

    # Track activity session
    try:
        from ..models.user_activity import UserActivity
        session = UserActivity(
            user_id=user.id,
            ip_address=flask_request.remote_addr,
        )
        db.session.add(session)
        db.session.commit()
        # Store session ID in Flask session for logout tracking
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
        from datetime import datetime, timezone
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
    """Return a bcrypt hash of *plaintext*."""
    return bcrypt.generate_password_hash(plaintext).decode("utf-8")


def check_password(plaintext: str, hashed: str) -> bool:
    """Return True if *plaintext* matches *hashed*."""
    return bcrypt.check_password_hash(hashed, plaintext)
