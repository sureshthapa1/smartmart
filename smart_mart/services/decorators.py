"""Route decorators for authentication and role-based access control."""

from functools import wraps

from flask import abort
from flask_login import current_user
from flask_login import login_required  # re-export for convenience

__all__ = ["login_required", "admin_required", "permission_required", "require_perm"]


def admin_required(f):
    """Restrict route to Admin users only."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.role != "admin":
            abort(403)
        return f(*args, **kwargs)
    return decorated


def permission_required(perm: str):
    """Restrict route to users with a specific permission.
    Admin users always pass. Staff users are checked against UserPermissions.
    """
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated(*args, **kwargs):
            if current_user.role == "admin":
                return f(*args, **kwargs)
            try:
                from ..models.user_permissions import UserPermissions
                p = UserPermissions.get_or_create(current_user.id)
                if not getattr(p, perm, False):
                    abort(403)
            except Exception:
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator


def require_perm(perm: str) -> None:
    """Inline permission check — call inside a view function.

    Aborts with 403 if the current staff user lacks *perm*.
    Admin users always pass. Identical to the _require_perm() helpers
    that were duplicated across blueprints.

    Usage::

        @bp.route("/sensitive")
        @login_required
        def sensitive_view():
            require_perm("can_manage_offers")
            ...
    """
    if current_user.role != "admin":
        try:
            from ..models.user_permissions import UserPermissions
            p = UserPermissions.get_or_create(current_user.id)
            if not getattr(p, perm, False):
                abort(403)
        except Exception:
            abort(403)
