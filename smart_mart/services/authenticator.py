"""Authentication service — login, logout, and password hashing."""

from __future__ import annotations

from flask_login import login_user, logout_user

from ..extensions import bcrypt, db
from ..models.user import User


def login(username: str, password: str) -> User | None:
    """Verify credentials and create a session.

    Returns the User on success, None on failure.
    """
    user: User | None = db.session.execute(
        db.select(User).filter_by(username=username)
    ).scalar_one_or_none()

    if user is None:
        return None

    if not check_password(password, user.password_hash):
        return None

    login_user(user)
    return user


def logout() -> None:
    """Invalidate the current session."""
    logout_user()


def hash_password(plaintext: str) -> str:
    """Return a bcrypt hash of *plaintext*."""
    return bcrypt.generate_password_hash(plaintext).decode("utf-8")


def check_password(plaintext: str, hashed: str) -> bool:
    """Return True if *plaintext* matches *hashed*."""
    return bcrypt.check_password_hash(hashed, plaintext)
