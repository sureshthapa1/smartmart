"""Stock Transfers blueprint."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from ...extensions import db
from ...models.product import Product
from ...models.operations import Branch
from ...services import transfer_manager
from ...services.decorators import admin_required

transfers_bp = Blueprint("transfers", __name__, url_prefix="/transfers")


@transfers_bp.route("/")
@admin_required
def list_transfers():
    transfers = transfer_manager.list_transfers()
    return render_template("transfers/list.html", transfers=transfers)


@transfers_bp.route("/create", methods=["GET", "POST"])
@admin_required
def create_transfer():
    branches = db.session.execute(db.select(Branch).where(Branch.is_active == True).order_by(Branch.name)).scalars().all()
    products = db.session.execute(db.select(Product).where(Product.quantity > 0).order_by(Product.name)).scalars().all()
    if request.method == "POST":
        from_id = request.form.get("from_branch_id", "")
        to_id = request.form.get("to_branch_id", "")
        notes = request.form.get("notes", "").strip() or None
        items = []
        idx = 0
        while True:
            pid = request.form.get(f"items[{idx}][product_id]")
            if pid is None:
                break
            try:
                qty = int(request.form.get(f"items[{idx}][quantity]", 0))
                if int(pid) > 0 and qty > 0:
                    items.append({"product_id": int(pid), "quantity": qty})
            except (ValueError, TypeError):
                pass
            idx += 1
        if not from_id or not to_id or not items:
            flash("Both branches and at least one item are required.", "danger")
            return render_template("transfers/form.html", branches=branches, products=products)
        try:
            t = transfer_manager.create_transfer(int(from_id), int(to_id), current_user.id, items, notes)
            flash(f"Transfer #{t.id} created.", "success")
            return redirect(url_for("transfers.transfer_detail", transfer_id=t.id))
        except ValueError as e:
            flash(str(e), "danger")
    return render_template("transfers/form.html", branches=branches, products=products)


@transfers_bp.route("/<int:transfer_id>")
@admin_required
def transfer_detail(transfer_id):
    from ...models.stock_transfer import StockTransfer
    t = db.get_or_404(StockTransfer, transfer_id)
    return render_template("transfers/detail.html", transfer=t)


@transfers_bp.route("/<int:transfer_id>/complete", methods=["POST"])
@admin_required
def complete_transfer(transfer_id):
    try:
        t = transfer_manager.complete_transfer(transfer_id)
        flash(f"Transfer #{t.id} completed. Stock deducted.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("transfers.transfer_detail", transfer_id=transfer_id))


@transfers_bp.route("/<int:transfer_id>/cancel", methods=["POST"])
@admin_required
def cancel_transfer(transfer_id):
    try:
        transfer_manager.cancel_transfer(transfer_id)
        flash("Transfer cancelled.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("transfers.list_transfers"))
