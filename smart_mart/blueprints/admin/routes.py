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


# ── Data Management ──────────────────────────────────────────────────────

@admin_bp.route("/data-management")
@admin_required
def data_management():
    from ...extensions import db
    from ...models.sale import Sale
    from ...models.purchase import Purchase
    from ...models.expense import Expense
    from ...models.stock_movement import StockMovement
    from ...models.product import Product
    from ...models.supplier import Supplier
    from ...models.category import Category

    counts = {
        "sales": db.session.execute(db.select(db.func.count(Sale.id))).scalar() or 0,
        "purchases": db.session.execute(db.select(db.func.count(Purchase.id))).scalar() or 0,
        "expenses": db.session.execute(db.select(db.func.count(Expense.id))).scalar() or 0,
        "stock_movements": db.session.execute(db.select(db.func.count(StockMovement.id))).scalar() or 0,
        "products": db.session.execute(db.select(db.func.count(Product.id))).scalar() or 0,
        "suppliers": db.session.execute(db.select(db.func.count(Supplier.id))).scalar() or 0,
        "categories": db.session.execute(db.select(db.func.count(Category.id))).scalar() or 0,
    }
    return render_template("admin/data_management.html", counts=counts)


@admin_bp.route("/data-management/clear", methods=["POST"])
@admin_required
def clear_data():
    from ...extensions import db
    from ...models.sale import Sale, SaleItem
    from ...models.purchase import Purchase, PurchaseItem
    from ...models.expense import Expense
    from ...models.stock_movement import StockMovement
    from ...models.product import Product
    from ...models.supplier import Supplier
    from ...models.category import Category

    section = request.form.get("section", "")
    confirm = request.form.get("confirm", "")

    if confirm != "DELETE":
        flash("Confirmation text did not match. You must type DELETE exactly.", "danger")
        return redirect(url_for("admin.data_management"))

    try:
        if section == "sales":
            db.session.execute(db.delete(SaleItem))
            db.session.execute(db.delete(Sale))
            db.session.commit()
            flash("All sales records cleared.", "success")

        elif section == "purchases":
            db.session.execute(db.delete(PurchaseItem))
            db.session.execute(db.delete(Purchase))
            db.session.commit()
            flash("All purchase records cleared.", "success")

        elif section == "expenses":
            db.session.execute(db.delete(Expense))
            db.session.commit()
            flash("All expense records cleared.", "success")

        elif section == "stock_movements":
            db.session.execute(db.delete(StockMovement))
            db.session.commit()
            flash("Stock movement history cleared.", "success")

        elif section == "products":
            db.session.execute(db.delete(SaleItem))
            db.session.execute(db.delete(Sale))
            db.session.execute(db.delete(PurchaseItem))
            db.session.execute(db.delete(Purchase))
            db.session.execute(db.delete(StockMovement))
            db.session.execute(db.delete(Product))
            db.session.commit()
            flash("All products and related records cleared.", "success")

        elif section == "suppliers":
            db.session.execute(db.delete(PurchaseItem))
            db.session.execute(db.delete(Purchase))
            db.session.execute(db.delete(Supplier))
            db.session.commit()
            flash("All suppliers cleared.", "success")

        elif section == "categories":
            db.session.execute(db.delete(Category))
            db.session.commit()
            flash("All categories cleared.", "success")

        elif section == "all":
            db.session.execute(db.delete(SaleItem))
            db.session.execute(db.delete(Sale))
            db.session.execute(db.delete(PurchaseItem))
            db.session.execute(db.delete(Purchase))
            db.session.execute(db.delete(Expense))
            db.session.execute(db.delete(StockMovement))
            db.session.execute(db.delete(Product))
            db.session.execute(db.delete(Supplier))
            db.session.execute(db.delete(Category))
            db.session.commit()
            flash("⚠️ All data has been permanently cleared.", "warning")

        else:
            flash(f"Unknown section: {section}", "danger")

    except Exception as e:
        db.session.rollback()
        flash(f"Error: {e}", "danger")

    return redirect(url_for("admin.data_management"))


# ── Staff Permissions ────────────────────────────────────────────────────

PERMISSION_GROUPS = {
    "Inventory": [
        ("can_view_inventory", "View Products"),
        ("can_add_product", "Add Products"),
        ("can_edit_product", "Edit Products"),
        ("can_delete_product", "Delete Products"),
        ("can_adjust_stock", "Adjust Stock"),
        ("can_bulk_upload_products", "Bulk Upload Products"),
    ],
    "Sales": [
        ("can_view_sales", "View Sales"),
        ("can_create_sale", "Create Sales"),
        ("can_delete_sale", "Delete / Reverse Sales"),
        ("can_give_discount", "Give Discounts"),
        ("can_download_invoice", "Download Invoices"),
    ],
    "Purchases": [
        ("can_view_purchases", "View Purchases"),
        ("can_create_purchase", "Create Purchases"),
        ("can_bulk_upload_purchases", "Bulk Upload Purchases"),
        ("can_manage_suppliers", "Manage Suppliers"),
    ],
    "Other": [
        ("can_view_alerts", "View Alerts"),
        ("can_view_dashboard", "View Dashboard"),
    ],
}


@admin_bp.route("/permissions")
@admin_required
def staff_permissions():
    from ...models.user import User
    from ...models.user_permissions import UserPermissions
    from ...extensions import db
    staff_users = db.session.execute(
        db.select(User).filter_by(role="staff").order_by(User.username)
    ).scalars().all()
    for u in staff_users:
        UserPermissions.get_or_create(u.id)
    return render_template("admin/staff_permissions.html",
                           staff_users=staff_users,
                           permission_groups=PERMISSION_GROUPS)


@admin_bp.route("/permissions/<int:user_id>", methods=["GET", "POST"])
@admin_required
def edit_permissions(user_id):
    from ...models.user import User
    from ...models.user_permissions import UserPermissions
    from ...extensions import db
    user = db.get_or_404(User, user_id)
    perms = UserPermissions.get_or_create(user_id)

    if request.method == "POST":
        for group_perms in PERMISSION_GROUPS.values():
            for perm_key, _ in group_perms:
                setattr(perms, perm_key, request.form.get(perm_key) == "on")
        db.session.commit()
        flash(f"Permissions updated for {user.username}.", "success")
        return redirect(url_for("admin.staff_permissions"))

    return render_template("admin/edit_permissions.html",
                           user=user, perms=perms,
                           permission_groups=PERMISSION_GROUPS)


# ── Staff Activity ────────────────────────────────────────────────────────

@admin_bp.route("/staff-activity")
@admin_required
def staff_activity():
    from ...models.user import User
    from ...models.user_activity import UserActivity
    from ...extensions import db
    from datetime import datetime, timezone, timedelta

    users = db.session.execute(
        db.select(User).filter_by(role="staff").order_by(User.username)
    ).scalars().all()

    activity_data = []
    for u in users:
        last_session = db.session.execute(
            db.select(UserActivity).filter_by(user_id=u.id)
            .order_by(UserActivity.login_at.desc()).limit(1)
        ).scalar_one_or_none()

        total_sessions = db.session.execute(
            db.select(db.func.count(UserActivity.id)).filter_by(user_id=u.id)
        ).scalar() or 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        sessions_30d = db.session.execute(
            db.select(UserActivity).filter(
                UserActivity.user_id == u.id,
                UserActivity.login_at >= cutoff
            )
        ).scalars().all()

        total_minutes = sum(s.duration_minutes for s in sessions_30d)
        total_page_views = sum(s.page_views or 0 for s in sessions_30d)
        is_online = last_session and last_session.logout_at is None

        activity_data.append({
            "user": u,
            "is_online": is_online,
            "last_login": last_session.login_at if last_session else None,
            "last_logout": last_session.logout_at if last_session else None,
            "total_sessions": total_sessions,
            "total_minutes_30d": total_minutes,
            "total_page_views_30d": total_page_views,
            "sessions_30d": len(sessions_30d),
        })

    recent = db.session.execute(
        db.select(UserActivity)
        .join(User, UserActivity.user_id == User.id)
        .filter(User.role == "staff")
        .order_by(UserActivity.login_at.desc())
        .limit(20)
    ).scalars().all()

    return render_template("admin/staff_activity.html",
                           activity_data=activity_data, recent=recent)
