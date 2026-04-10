from flask import Blueprint, flash, redirect, render_template, request, url_for, abort
from flask_login import current_user

from ...services.decorators import login_required
from ...services import returns_manager

returns_bp = Blueprint("returns", __name__, url_prefix="/returns")


@returns_bp.route("/")
@login_required
def list_returns():
    if current_user.role != "admin":
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(current_user.id)
        if not p.can_view_returns:
            abort(403)
    from ...extensions import db
    from ...models.sale_return import SaleReturn
    from ...models.sale import Sale
    from sqlalchemy import func, and_
    from datetime import date

    q = request.args.get("q", "").strip() or None
    start_raw = request.args.get("start_date", "").strip() or None
    end_raw = request.args.get("end_date", "").strip() or None
    page = request.args.get("page", 1, type=int)
    per_page = 25

    stmt = (db.select(SaleReturn)
            .join(Sale, Sale.id == SaleReturn.sale_id)
            .order_by(SaleReturn.created_at.desc()))

    if q:
        stmt = stmt.where(
            func.lower(Sale.customer_name).like(f"%{q.lower()}%") |
            func.lower(Sale.invoice_number).like(f"%{q.lower()}%")
        )
    if start_raw:
        try:
            stmt = stmt.where(func.date(SaleReturn.created_at) >= date.fromisoformat(start_raw))
        except ValueError:
            pass
    if end_raw:
        try:
            stmt = stmt.where(func.date(SaleReturn.created_at) <= date.fromisoformat(end_raw))
        except ValueError:
            pass

    all_returns = db.session.execute(stmt).scalars().all()
    total = len(all_returns)
    total_refund = sum(float(r.refund_amount) for r in all_returns)
    returns = all_returns[(page - 1) * per_page: page * per_page]

    return render_template("returns/list.html", returns=returns,
                           total=total, total_refund=total_refund,
                           q=q or "", start_date=start_raw or "",
                           end_date=end_raw or "", page=page, per_page=per_page)


@returns_bp.route("/sale/<int:sale_id>/create", methods=["GET", "POST"])
@login_required
def create_return(sale_id):
    if current_user.role != "admin":
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(current_user.id)
        if not p.can_create_return:
            abort(403)
    sale = returns_manager.get_sale(sale_id)
    returnable_items = returns_manager.returnable_items_for_sale(sale_id)

    if request.method == "POST":
        item_quantities = []
        for row in returnable_items:
            sale_item = row["sale_item"]
            qty = request.form.get(f"return_qty_{sale_item.id}", "0").strip() or "0"
            try:
                parsed_qty = int(qty)
            except ValueError:
                parsed_qty = 0
            item_quantities.append({"sale_item_id": sale_item.id, "quantity": parsed_qty})

        try:
            sale_return = returns_manager.create_return(
                sale_id=sale.id,
                user_id=current_user.id,
                item_quantities=item_quantities,
                refund_mode=request.form.get("refund_mode", "cash"),
                reason=request.form.get("reason"),
                notes=request.form.get("notes"),
            )
            flash(f"Return #{sale_return.id} created successfully.", "success")
            return redirect(url_for("returns.return_detail", return_id=sale_return.id))
        except ValueError as exc:
            flash(str(exc), "danger")

    return render_template(
        "returns/create.html",
        sale=sale,
        returnable_items=returnable_items,
    )


@returns_bp.route("/<int:return_id>")
@login_required
def return_detail(return_id):
    sale_return = returns_manager.get_return(return_id)
    return render_template("returns/detail.html", sale_return=sale_return)
