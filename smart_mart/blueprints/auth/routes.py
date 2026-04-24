"""Auth blueprint — login, logout, password change, and password reset routes."""

from datetime import datetime
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ...services import authenticator

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        from ...services.authenticator import is_rate_limited, _get_client_ip
        if is_rate_limited(_get_client_ip()):
            flash("Too many failed login attempts. Please wait 10 minutes and try again.", "danger")
            return render_template("auth/login.html", now=datetime.now())

        user = authenticator.login(username, password)
        if user is not None:
            return redirect(url_for("dashboard.index"))

        flash("Invalid username or password.", "danger")

    return render_template("auth/login.html", now=datetime.now())


@auth_bp.route("/logout")
def logout():
    authenticator.logout()
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")

        from ...extensions import bcrypt
        from ...models.user import User
        from ...extensions import db

        user = db.session.get(User, current_user.id)

        if not bcrypt.check_password_hash(user.password_hash, current_pw):
            flash("Current password is incorrect.", "danger")
            return render_template("auth/change_password.html")
        if len(new_pw) < 6:
            flash("New password must be at least 6 characters.", "danger")
            return render_template("auth/change_password.html")
        if new_pw != confirm_pw:
            flash("New passwords do not match.", "danger")
            return render_template("auth/change_password.html")

        user.password_hash = bcrypt.generate_password_hash(new_pw).decode("utf-8")
        db.session.commit()
        flash("Password changed successfully.", "success")
        return redirect(url_for("dashboard.index"))

    return render_template("auth/change_password.html")


# ── Password Reset (token-based, no email required) ───────────────────────────

@auth_bp.route("/reset-password/request", methods=["GET", "POST"])
def request_password_reset():
    """Step 1 — admin generates a reset token for a username.
    The token is written to the server log; the operator relays it to the user.
    """
    from flask import current_app
    from ...services.password_reset_service import generate_reset_token
    from ...models.user import User
    from ...extensions import db

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        user = db.session.execute(
            db.select(User).where(User.username == username)
        ).scalar_one_or_none()

        # Always show the same message to prevent username enumeration
        if user:
            token = generate_reset_token(user.id)
            reset_url = url_for("auth.reset_password_confirm", token=token, _external=True)
            current_app.logger.warning(
                "PASSWORD RESET requested for user '%s' (id=%s). "
                "Reset URL (valid 30 min): %s",
                user.username, user.id, reset_url,
            )

        flash(
            "If that username exists, a reset link has been written to the server log. "
            "Ask your system administrator to retrieve it.",
            "info",
        )
        return redirect(url_for("auth.login"))

    return render_template("auth/request_reset.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password_confirm(token: str):
    """Step 2 — user follows the link and sets a new password."""
    from ...services.password_reset_service import verify_reset_token
    from ...services.authenticator import hash_password
    from ...extensions import db

    user = verify_reset_token(token)
    if user is None:
        flash("This reset link is invalid or has expired (30-minute limit).", "danger")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")

        if len(new_pw) < 8:
            flash("Password must be at least 8 characters.", "danger")
            return render_template("auth/reset_password.html", token=token)
        if new_pw != confirm_pw:
            flash("Passwords do not match.", "danger")
            return render_template("auth/reset_password.html", token=token)

        user.password_hash = hash_password(new_pw)
        db.session.commit()
        flash("Password reset successfully. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", token=token)
