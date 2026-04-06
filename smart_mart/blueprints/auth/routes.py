"""Auth blueprint — login and logout routes."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ...services import authenticator

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = authenticator.login(username, password)
        if user is not None:
            return redirect(url_for("dashboard.index"))

        flash("Invalid username or password.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/logout")
def logout():
    authenticator.logout()
    return redirect(url_for("auth.login"))
