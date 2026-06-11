# smart_mart/blueprints/auth/routes.py  — PATCH
# ==============================================
# FIND your existing login route and REPLACE it with this version.
# This adds: 5 attempts / minute rate limit, audit logging on failed login.

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from smart_mart.extensions import bcrypt, limiter
from smart_mart.models.user import User          # adjust import to match your model path
from smart_mart.models.audit_log import AuditLog  # adjust to your audit log model
from smart_mart.extensions import db
import datetime

auth_bp = Blueprint("auth", __name__, template_folder="templates")


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", error_message="Too many login attempts. Please wait 1 minute.")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()

        if user and bcrypt.check_password_hash(user.password_hash, password):
            if not user.is_active:
                flash("Your account has been deactivated. Contact admin.", "danger")
                return redirect(url_for("auth.login"))

            login_user(user, remember=False)

            # Audit log successful login
            _log_action(user.id, "LOGIN_SUCCESS", f"User {username} logged in from {request.remote_addr}")

            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard.index"))
        else:
            # Audit log failed attempt
            user_id = user.id if user else None
            _log_action(user_id, "LOGIN_FAILED", f"Failed login for '{username}' from {request.remote_addr}")
            flash("Invalid username or password.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    _log_action(current_user.id, "LOGOUT", f"User {current_user.username} logged out")
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


def _log_action(user_id, action, description):
    """Helper — write to AuditLog if model exists, else just log."""
    try:
        entry = AuditLog(
            user_id=user_id,
            action=action,
            description=description,
            timestamp=datetime.datetime.utcnow(),
            ip_address=request.remote_addr,
        )
        db.session.add(entry)
        db.session.commit()
    except Exception:
        pass  # Don't crash login if audit log fails
