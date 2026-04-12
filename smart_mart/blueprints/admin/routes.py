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
        ("can_view_inventory", "View Products & Stock"),
        ("can_add_product", "Add Products"),
        ("can_edit_product", "Edit Products"),
        ("can_delete_product", "Delete Products"),
        ("can_adjust_stock", "Adjust Stock Manually"),
        ("can_bulk_upload_products", "Bulk Upload Products"),
        ("can_manage_categories", "Manage Categories"),
        ("can_manage_variants", "Manage Product Variants"),
        ("can_print_labels", "Print Price Labels"),
        ("can_view_stock_take", "View Stock Takes"),
        ("can_manage_stock_take", "Create & Complete Stock Takes"),
    ],
    "Sales": [
        ("can_view_sales", "View Sales List"),
        ("can_create_sale", "Create New Sales (POS)"),
        ("can_delete_sale", "Delete / Reverse Sales"),
        ("can_give_discount", "Apply Discounts"),
        ("can_download_invoice", "Download & Print Invoices"),
        ("can_view_customer_statement", "View Customer Statements"),
    ],
    "Returns": [
        ("can_view_returns", "View Sale Returns"),
        ("can_create_return", "Process Sale Returns"),
        ("can_view_supplier_returns", "View Supplier Returns"),
        ("can_manage_supplier_returns", "Create Supplier Returns"),
    ],
    "Purchases": [
        ("can_view_purchases", "View Purchases"),
        ("can_create_purchase", "Create Purchases"),
        ("can_bulk_upload_purchases", "Bulk Upload Purchases"),
        ("can_manage_suppliers", "View & Manage Suppliers"),
        ("can_view_purchase_orders", "View Purchase Orders"),
        ("can_manage_purchase_orders", "Create & Manage Purchase Orders"),
    ],
    "Customers": [
        ("can_view_customers", "View Customer List & Profiles"),
        ("can_manage_customers", "Add & Edit Customers"),
    ],
    "Finance": [
        ("can_manage_credits", "View & Record Credit Payments (Udharo)"),
        ("can_manage_cash_session", "Open & Close Cash Sessions"),
        ("can_view_expenses", "View Expenses"),
        ("can_manage_expenses", "Add & Edit Expenses"),
    ],
    "Reports": [
        ("can_view_reports", "View All Reports"),
        ("can_view_sales_report", "Sales Report"),
        ("can_view_profit_report", "Profitability Report"),
        ("can_view_stock_report", "Stock & Inventory Reports"),
        ("can_view_credit_report", "Credit / Udharo Report"),
    ],
    "Online Orders": [
        ("can_view_online_orders", "View Online Orders"),
        ("can_manage_online_orders", "Create & Update Online Orders"),
    ],
    "Promotions & Transfers": [
        ("can_view_promotions", "View Promotions"),
        ("can_manage_promotions", "Create & Edit Promotions"),
        ("can_view_transfers", "View Stock Transfers"),
        ("can_manage_transfers", "Create Stock Transfers"),
    ],
    "AI & Insights": [
        ("can_view_ai_insights", "View AI Insights & Analytics"),
        ("can_view_advisor", "View Business Advisor"),
    ],
    "General": [
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


# ── Backup & Restore ──────────────────────────────────────────────────────

@admin_bp.route("/backup")
@admin_required
def backup():
    from ...services import backup_service
    logs = backup_service.get_backup_logs()
    files = backup_service.list_backups()
    return render_template("admin/backup.html", logs=logs, files=files)


@admin_bp.route("/backup/create", methods=["POST"])
@admin_required
def create_backup():
    from ...services import backup_service
    from flask import Response
    try:
        result = backup_service.create_backup(user_id=current_user.id, backup_type="manual")
        # Offer as download
        return Response(
            result["json_data"],
            mimetype="application/json",
            headers={"Content-Disposition": f"attachment; filename={result['filename']}"},
        )
    except Exception as e:
        flash(f"Backup failed: {e}", "danger")
        return redirect(url_for("admin.backup"))


@admin_bp.route("/backup/delete", methods=["POST"])
@admin_required
def delete_backup():
    from ...services import backup_service
    filename = request.form.get("filename", "")
    if backup_service.delete_backup_file(filename):
        flash(f"Backup '{filename}' deleted.", "success")
    else:
        flash("Could not delete backup file.", "danger")
    return redirect(url_for("admin.backup"))


# ── Audit Log ─────────────────────────────────────────────────────────────

@admin_bp.route("/audit-log")
@admin_required
def audit_log():
    from ...services import audit_service
    from ...models.user import User
    from ...extensions import db as _db

    entity_type = request.args.get("entity_type", "").strip() or None
    user_id_raw = request.args.get("user_id", "").strip()
    user_id = int(user_id_raw) if user_id_raw.isdigit() else None
    page = request.args.get("page", 1, type=int)

    logs = audit_service.get_logs(entity_type=entity_type, user_id=user_id, page=page)
    users = _db.session.execute(_db.select(User).order_by(User.username)).scalars().all()
    entity_types = ["Product", "Sale", "Purchase", "User", "Expense", "StockTake",
                    "SupplierReturn", "Promotion"]
    return render_template("admin/audit_log.html",
                           logs=logs, users=users, entity_types=entity_types,
                           selected_entity=entity_type or "",
                           selected_user=user_id_raw, page=page)


@admin_bp.route("/sync-visit-counts", methods=["POST"])
@admin_required
def sync_visit_counts():
    """Sync customer visit_count from actual sales records."""
    from ...models.customer import Customer
    from ...models.sale import Sale
    from ...extensions import db
    from sqlalchemy import func

    customers = db.session.execute(db.select(Customer)).scalars().all()
    updated = 0
    for c in customers:
        actual = db.session.execute(
            db.select(func.count(Sale.id))
            .where(func.lower(Sale.customer_name) == c.name.lower())
        ).scalar() or 0
        if actual != c.visit_count:
            c.visit_count = actual
            updated += 1
    db.session.commit()
    flash(f"Visit counts synced — {updated} customer(s) updated.", "success")
    return redirect(url_for("admin.staff_activity"))
