"""Admin blueprint — user management routes."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ...services import user_manager
from ...services.decorators import admin_required

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/users")
@admin_required
def users():
    all_users = user_manager.list_users()
    return render_template("admin/users.html", users=all_users)


@admin_bp.route("/users/create", methods=["GET", "POST"])
@admin_required
def create_user():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "staff")
        try:
            user_manager.create_user(username, password, role)
            flash(f"User '{username}' created successfully.", "success")
            return redirect(url_for("admin.users"))
        except ValueError as e:
            flash(str(e), "danger")

    return render_template("admin/user_form.html", user=None, action="Create")


@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_user(user_id):
    from ...models.user import User
    from ...extensions import db

    user = db.get_or_404(User, user_id)

    if request.method == "POST":
        data = {}
        username = request.form.get("username", "").strip()
        role = request.form.get("role", "staff")
        if username:
            data["username"] = username
        data["role"] = role
        try:
            user_manager.update_user(user_id, data)
            flash(f"User '{username}' updated successfully.", "success")
            return redirect(url_for("admin.users"))
        except ValueError as e:
            flash(str(e), "danger")

    return render_template("admin/user_form.html", user=user, action="Edit")


@admin_bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def reset_password(user_id):
    new_password = request.form.get("new_password", "")
    try:
        user_manager.reset_password(user_id, new_password)
        flash("Password reset successfully.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    try:
        user_manager.delete_user(user_id, current_user.id)
        flash("User deleted successfully.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("admin.users"))
