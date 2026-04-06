"""Route decorators for authentication and role-based access control."""

from functools import wraps

from flask import abort
from flask_login import current_user
from flask_login import login_required  # re-export for convenience

__all__ = ["login_required", "admin_required"]


def admin_required(f):
    """Decorator that restricts a route to Admin users only.

    Unauthenticated requests are handled by @login_required first.
    Authenticated Staff users receive HTTP 403.
    """
    @login_required
    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user.role != "admin":
            abort(403)
        return f(*args, **kwargs)

    return decorated
