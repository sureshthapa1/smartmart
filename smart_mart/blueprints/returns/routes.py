from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ...services.decorators import login_required
from ...services import returns_manager

returns_bp = Blueprint("returns", __name__, url_prefix="/returns")


@returns_bp.route("/")
@login_required
def list_returns():
    returns = returns_manager.list_returns()
    return render_template("returns/list.html", returns=returns)


@returns_bp.route("/sale/<int:sale_id>/create", methods=["GET", "POST"])
@login_required
def create_return(sale_id):
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
