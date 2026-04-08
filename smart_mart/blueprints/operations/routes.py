from flask import Blueprint, Response, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ...extensions import db
from ...models.operations import CashSession, ProductBatch
from ...models.product import Product
from ...models.purchase import Purchase
from ...services.decorators import admin_required
from ...services import operations_manager

operations_bp = Blueprint("operations", __name__, url_prefix="/operations")


@operations_bp.route("/")
@admin_required
def index():
    credit_records = operations_manager.get_credit_records()
    supplier_rows = operations_manager.get_supplier_balances()
    open_session = operations_manager.get_open_cash_session(current_user.id)
    reorders = operations_manager.get_reorder_suggestions()
    notifications = operations_manager.ensure_notifications()[:10]
    loyalty = operations_manager.get_loyalty_summary()[:10]
    branches = operations_manager.list_branches()
    return render_template(
        "operations/index.html",
        credit_records=credit_records,
        supplier_rows=supplier_rows,
        open_session=open_session,
        reorders=reorders,
        notifications=notifications,
        loyalty=loyalty,
        branches=branches,
    )


@operations_bp.route("/credits", methods=["GET", "POST"])
@admin_required
def credits():
    if request.method == "POST":
        try:
            operations_manager.record_credit_payment(
                sale_id=int(request.form.get("sale_id", "0")),
                user_id=current_user.id,
                amount=float(request.form.get("amount", "0") or 0),
                payment_mode=request.form.get("payment_mode", "cash"),
                note=request.form.get("note"),
            )
            flash("Credit payment recorded.", "success")
        except ValueError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("operations.credits"))
    return render_template("operations/credits.html", records=operations_manager.get_credit_records())


@operations_bp.route("/suppliers", methods=["GET", "POST"])
@admin_required
def suppliers():
    if request.method == "POST":
        try:
            purchase_id = request.form.get("purchase_id", "").strip()
            operations_manager.record_supplier_payment(
                supplier_id=int(request.form.get("supplier_id", "0")),
                purchase_id=int(purchase_id) if purchase_id else None,
                user_id=current_user.id,
                amount=float(request.form.get("amount", "0") or 0),
                payment_mode=request.form.get("payment_mode", "cash"),
                note=request.form.get("note"),
            )
            flash("Supplier payment recorded.", "success")
        except ValueError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("operations.suppliers"))
    return render_template(
        "operations/suppliers.html",
        supplier_rows=operations_manager.get_supplier_balances(),
        purchases=db.session.execute(db.select(Purchase).order_by(Purchase.purchase_date.desc())).scalars().all(),
    )


@operations_bp.route("/closing", methods=["GET", "POST"])
@admin_required
def closing():
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "open":
                operations_manager.open_cash_session(
                    user_id=current_user.id,
                    opening_cash=float(request.form.get("opening_cash", "0") or 0),
                    notes=request.form.get("notes"),
                )
                flash("Cash session opened.", "success")
            elif action == "close":
                operations_manager.close_cash_session(
                    session_id=int(request.form.get("session_id", "0")),
                    closing_cash=float(request.form.get("closing_cash", "0") or 0),
                    notes=request.form.get("notes"),
                )
                flash("Cash session closed.", "success")
        except ValueError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("operations.closing"))
    session = operations_manager.get_open_cash_session(current_user.id)
    sessions = db.session.execute(db.select(CashSession).order_by(CashSession.opened_at.desc())).scalars().all()
    return render_template("operations/closing.html", session=session, sessions=sessions)


@operations_bp.route("/inventory-tools", methods=["GET", "POST"])
@admin_required
def inventory_tools():
    if request.method == "POST":
        try:
            operations_manager.update_inventory_profile(
                product_id=int(request.form.get("product_id", "0")),
                barcode=request.form.get("barcode"),
                reorder_level=int(request.form.get("reorder_level", "10") or 10),
                shelf_location=request.form.get("shelf_location"),
            )
            flash("Inventory profile updated.", "success")
        except ValueError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("operations.inventory_tools"))
    products = db.session.execute(db.select(Product).order_by(Product.name)).scalars().all()
    profiles = {p.id: operations_manager._profile_for(p.id) for p in products}
    batches = db.session.execute(db.select(ProductBatch).order_by(ProductBatch.created_at.desc())).scalars().all()
    return render_template("operations/inventory_tools.html", products=products, profiles=profiles, batches=batches)


@operations_bp.route("/reorders")
@admin_required
def reorders():
    return render_template("operations/reorders.html", suggestions=operations_manager.get_reorder_suggestions())


@operations_bp.route("/notifications", methods=["GET", "POST"])
@admin_required
def notifications():
    if request.method == "POST":
        operations_manager.mark_notification_read(int(request.form.get("notification_id", "0")))
        return redirect(url_for("operations.notifications"))
    notifications = operations_manager.ensure_notifications()
    return render_template("operations/notifications.html", notifications=notifications)


@operations_bp.route("/loyalty")
@admin_required
def loyalty():
    return render_template("operations/loyalty.html", loyalty=operations_manager.get_loyalty_summary())


@operations_bp.route("/branches", methods=["GET", "POST"])
@admin_required
def branches():
    if request.method == "POST":
        try:
            operations_manager.create_branch(
                name=request.form.get("name", ""),
                code=request.form.get("code", ""),
                address=request.form.get("address"),
            )
            flash("Branch created.", "success")
        except Exception as exc:
            flash(str(exc), "danger")
        return redirect(url_for("operations.branches"))
    return render_template("operations/branches.html", branches=operations_manager.list_branches())


@operations_bp.route("/backup/export")
@admin_required
def backup_export():
    return Response(
        operations_manager.export_backup_snapshot(),
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=smart_mart_backup.json"},
    )
