"""Auth blueprint — login, logout, and password change routes."""

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
