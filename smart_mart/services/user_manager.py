"""User management service — create, update, reset password, delete, and list users."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models.user import User
from . import authenticator


def create_user(username: str, password: str, role: str,
                commission_rate: float = 0.0, email: str | None = None) -> User:
    """Create a new user with a hashed password.
    Staff users automatically get minimal default permissions.
    Raises ValueError if the username already exists or password is too weak.
    """
    pw_errors = authenticator.validate_password_strength(password)
    if pw_errors:
        raise ValueError("Password too weak: " + " ".join(pw_errors))

    user = User(
        username=username,
        password_hash=authenticator.hash_password(password),
        role=role,
        commission_rate=commission_rate,
        email=email or None,
    )
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ValueError(f"Username '{username}' is already taken.")

    # Auto-create minimal permissions for staff
    if role == "staff":
        from ..models.user_permissions import UserPermissions
        UserPermissions.get_or_create(user.id)

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
    if "commission_rate" in data:
        user.commission_rate = data["commission_rate"]
    if "email" in data:
        user.email = data["email"] or None
    if "is_active" in data:
        user.is_active = bool(data["is_active"])

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ValueError(f"Username '{data.get('username')}' is already taken.")
    return user


def reset_password(user_id: int, new_password: str) -> None:
    """Hash and store a new password for the given user.
    Raises ValueError if the password does not meet strength requirements.
    """
    pw_errors = authenticator.validate_password_strength(new_password)
    if pw_errors:
        raise ValueError("Password too weak: " + " ".join(pw_errors))
    user: User = db.get_or_404(User, user_id)
    user.password_hash = authenticator.hash_password(new_password)
    db.session.commit()


def delete_user(user_id: int, current_user_id: int) -> None:
    """Delete a user and all their dependent records."""
    if user_id == current_user_id:
        raise ValueError("You cannot delete your own account.")
    user: User = db.get_or_404(User, user_id)

    from sqlalchemy import delete as _delete, update as _update

    # Delete/nullify all FK references to this user before deleting the user row
    try:
        from ..models.user_permissions import UserPermissions
        db.session.execute(_delete(UserPermissions).where(UserPermissions.user_id == user_id))
    except Exception:
        pass
    try:
        from ..models.user_activity import UserActivity
        db.session.execute(_delete(UserActivity).where(UserActivity.user_id == user_id))
    except Exception:
        pass
    try:
        from ..models.login_attempt import LoginAttempt
        db.session.execute(_delete(LoginAttempt).where(LoginAttempt.username == user.username))
    except Exception:
        pass
    try:
        from ..models.sales_target import SalesTarget
        db.session.execute(_delete(SalesTarget).where(SalesTarget.user_id == user_id))
    except Exception:
        pass
    try:
        from ..models.shift import Shift
        db.session.execute(_delete(Shift).where(Shift.user_id == user_id))
    except Exception:
        pass
    try:
        from ..models.audit_log import AuditLog
        db.session.execute(_delete(AuditLog).where(AuditLog.user_id == user_id))
    except Exception:
        pass
    try:
        from ..models.operations import CashSession
        db.session.execute(_delete(CashSession).where(CashSession.user_id == user_id))
    except Exception:
        pass
    # Nullify user_id on financial records rather than deleting them
    try:
        from ..models.sale import Sale
        db.session.execute(_update(Sale).where(Sale.user_id == user_id).values(user_id=None))
    except Exception:
        pass
    try:
        from ..models.purchase import Purchase
        db.session.execute(_update(Purchase).where(Purchase.created_by == user_id).values(created_by=None))
    except Exception:
        pass
    try:
        from ..models.expense import Expense
        db.session.execute(_update(Expense).where(Expense.created_by == user_id).values(created_by=None))
    except Exception:
        pass
    try:
        from ..models.stock_movement import StockMovement
        db.session.execute(_update(StockMovement).where(StockMovement.created_by == user_id).values(created_by=None))
    except Exception:
        pass

    db.session.flush()
    db.session.delete(user)
    db.session.commit()


def list_users() -> list[User]:
    """Return all users ordered by username."""
    return db.session.execute(
        db.select(User).order_by(User.username)
    ).scalars().all()
