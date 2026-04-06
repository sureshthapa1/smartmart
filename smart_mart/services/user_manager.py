"""User management service — create, update, reset password, delete, and list users."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models.user import User
from . import authenticator


def create_user(username: str, password: str, role: str) -> User:
    """Create a new user with a hashed password.

    Raises ValueError if the username already exists.
    """
    user = User(
        username=username,
        password_hash=authenticator.hash_password(password),
        role=role,
    )
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ValueError(f"Username '{username}' is already taken.")
    return user


def update_user(user_id: int, data: dict) -> User:
    """Update a user's username and/or role.

    Raises ValueError if the new username is already taken.
    """
    user: User = db.get_or_404(User, user_id)

    if "username" in data:
        user.username = data["username"]
    if "role" in data:
        user.role = data["role"]

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ValueError(f"Username '{data.get('username')}' is already taken.")
    return user


def reset_password(user_id: int, new_password: str) -> None:
    """Hash and store a new password for the given user."""
    user: User = db.get_or_404(User, user_id)
    user.password_hash = authenticator.hash_password(new_password)
    db.session.commit()


def delete_user(user_id: int, current_user_id: int) -> None:
    """Delete a user by ID.

    Raises ValueError if the user attempts to delete their own account.
    """
    if user_id == current_user_id:
        raise ValueError("You cannot delete your own account.")
    user: User = db.get_or_404(User, user_id)
    db.session.delete(user)
    db.session.commit()


def list_users() -> list[User]:
    """Return all users ordered by username."""
    return db.session.execute(
        db.select(User).order_by(User.username)
    ).scalars().all()
