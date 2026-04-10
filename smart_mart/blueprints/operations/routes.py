from datetime import date

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
    credit_data = operations_manager.get_credit_records(per_page=10)
    supplier_data = operations_manager.get_supplier_balances(per_page=10)
    open_session = operations_manager.get_open_cash_session(current_user.id)
    reorders = operations_manager.get_reorder_suggestions()
    notifications = operations_manager.ensure_notifications()[:10]
    loyalty = operations_manager.get_loyalty_summary()[:10]
    branches = operations_manager.list_branches()
    from ...services.credit_risk_service import get_risk_summary
    risk_summary = get_risk_summary()
    return render_template(
        "operations/index.html",
        credit_records=credit_data["records"],
        supplier_rows=supplier_data["rows"],
        open_session=open_session,
        reorders=reorders,
        notifications=notifications,
        loyalty=loyalty,
        branches=branches,
        risky_count=risk_summary["risky"],
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
    page = request.args.get("page", 1, type=int)
    data = operations_manager.get_credit_records(page=page)
    return render_template("operations/credits.html", data=data, today=date.today())


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
    page = request.args.get("page", 1, type=int)
    data = operations_manager.get_supplier_balances(page=page)
    purchases = db.session.execute(db.select(Purchase).order_by(Purchase.purchase_date.desc())).scalars().all()
    return render_template("operations/suppliers.html", data=data, purchases=purchases)


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
        action = request.form.get("action", "mark_one")
        if action == "mark_all":
            operations_manager.mark_all_notifications_read()
        else:
            operations_manager.mark_notification_read(int(request.form.get("notification_id", "0")))
        return redirect(url_for("operations.notifications"))
    notifications = operations_manager.ensure_notifications()
    return render_template("operations/notifications.html", notifications=notifications)


@operations_bp.route("/loyalty", methods=["GET", "POST"])
@admin_required
def loyalty():
    if request.method == "POST":
        try:
            operations_manager.redeem_loyalty_points(
                customer_name=request.form.get("customer_name", ""),
                points=int(request.form.get("points", "0") or 0),
                note=request.form.get("note"),
            )
            flash("Points redeemed successfully.", "success")
        except ValueError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("operations.loyalty"))
    return render_template("operations/loyalty.html", loyalty=operations_manager.get_loyalty_summary())


@operations_bp.route("/branches", methods=["GET", "POST"])
@admin_required
def branches():
    if request.method == "POST":
        action = request.form.get("action", "create")
        try:
            if action == "create":
                operations_manager.create_branch(
                    name=request.form.get("name", ""),
                    code=request.form.get("code", ""),
                    address=request.form.get("address"),
                )
                flash("Branch created.", "success")
            elif action == "edit":
                operations_manager.update_branch(
                    branch_id=int(request.form.get("branch_id", "0")),
                    name=request.form.get("name", ""),
                    code=request.form.get("code", ""),
                    address=request.form.get("address"),
                )
                flash("Branch updated.", "success")
            elif action == "toggle":
                branch = operations_manager.toggle_branch_active(int(request.form.get("branch_id", "0")))
                flash(f"Branch {'activated' if branch.is_active else 'deactivated'}.", "success")
        except Exception as exc:
            flash(str(exc), "danger")
        return redirect(url_for("operations.branches"))
    return render_template("operations/branches.html", branches=operations_manager.list_branches())


@operations_bp.route("/notifications/log")
@admin_required
def notification_log():
    from ...models.notification_log import NotificationLog
    logs = db.session.execute(
        db.select(NotificationLog).order_by(NotificationLog.created_at.desc()).limit(100)
    ).scalars().all()
    return render_template("operations/notification_log.html", logs=logs)


@operations_bp.route("/backup/export")
@admin_required
def backup_export():
    return Response(
        operations_manager.export_backup_snapshot(),
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=smart_mart_backup.json"},
    )


@operations_bp.route("/eod", methods=["GET"])
def eod_summary():
    from datetime import date
    from ...services.eod_summary import get_eod_summary
    date_raw = request.args.get("date", "")
    try:
        target = date.fromisoformat(date_raw) if date_raw else date.today()
    except ValueError:
        target = date.today()
    summary = get_eod_summary(target)
    return render_template("operations/eod_summary.html", summary=summary, today=date.today())


@operations_bp.route("/shifts", methods=["GET", "POST"])
@admin_required
def shifts():
    from ...services.shift_manager import open_shift, close_shift, list_shifts, get_open_shift
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "open":
                open_shift(current_user.id, float(request.form.get("opening_cash", 0) or 0),
                           request.form.get("notes"))
                flash("Shift opened.", "success")
            elif action == "close":
                close_shift(int(request.form.get("shift_id", 0)),
                            float(request.form.get("closing_cash", 0) or 0),
                            request.form.get("notes"))
                flash("Shift closed.", "success")
        except ValueError as e:
            flash(str(e), "danger")
        return redirect(url_for("operations.shifts"))
    open_shift_obj = get_open_shift(current_user.id)
    all_shifts = list_shifts()
    return render_template("operations/shifts.html", open_shift=open_shift_obj, shifts=all_shifts)


@operations_bp.route("/credit-risk")
@admin_required
def credit_risk():
    from ...services.credit_risk_service import get_all_customer_risk_scores, get_risk_summary
    scores = get_all_customer_risk_scores()
    summary = get_risk_summary()
    return render_template("operations/credit_risk.html", scores=scores, summary=summary)


@operations_bp.route("/credit-risk/<path:customer_name>")
@admin_required
def credit_risk_detail(customer_name):
    from ...services.credit_risk_service import calculate_risk_score
    data = calculate_risk_score(customer_name)
    return render_template("operations/credit_risk_detail.html", data=data, today=date.today())


@operations_bp.route("/credit-risk/recalculate", methods=["POST"])
@admin_required
def credit_risk_recalculate():
    from ...services.credit_risk_service import recalculate_all
    count = recalculate_all()
    flash(f"Risk scores recalculated for {count} customer(s).", "success")
    return redirect(url_for("operations.credit_risk"))


@operations_bp.route("/credit-risk/override", methods=["POST"])
@admin_required
def credit_risk_override():
    from ...services.credit_risk_service import set_override
    customer_name = request.form.get("customer_name", "").strip()
    override_tier = request.form.get("override_tier", "").strip() or None
    if not customer_name:
        flash("Customer name is required.", "danger")
        return redirect(url_for("operations.credit_risk"))
    try:
        set_override(customer_name, override_tier, current_user.id)
        if override_tier:
            flash(f"Override set to '{override_tier}' for {customer_name}.", "success")
        else:
            flash(f"Override cleared for {customer_name}.", "info")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("operations.credit_risk"))
