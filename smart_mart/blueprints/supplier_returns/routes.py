"""Supplier Returns blueprint — return goods to supplier (Feature #5)."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ...extensions import db
from ...models.product import Product
from ...models.supplier import Supplier
from ...models.purchase import Purchase
from ...services import supplier_return_service
from ...services.decorators import admin_required, login_required

supplier_returns_bp = Blueprint("supplier_returns", __name__, url_prefix="/supplier-returns")


@supplier_returns_bp.route("/")
@admin_required
def list_returns():
    page = request.args.get("page", 1, type=int)
    returns = supplier_return_service.list_supplier_returns(page=page)
    return render_template("supplier_returns/list.html", returns=returns, page=page)


@supplier_returns_bp.route("/create", methods=["GET", "POST"])
@admin_required
def create_return():
    suppliers = db.session.execute(db.select(Supplier).order_by(Supplier.name)).scalars().all()
    products = db.session.execute(db.select(Product).order_by(Product.name)).scalars().all()
    purchases = db.session.execute(
        db.select(Purchase).order_by(Purchase.purchase_date.desc()).limit(50)
    ).scalars().all()

    if request.method == "POST":
        supplier_id = request.form.get("supplier_id", "").strip()
        purchase_id = request.form.get("purchase_id", "").strip() or None
        reason = request.form.get("reason", "").strip() or None
        notes = request.form.get("notes", "").strip() or None
        items = _parse_items(request.form)

        if not supplier_id:
            flash("Please select a supplier.", "danger")
            return render_template("supplier_returns/create.html",
                                   suppliers=suppliers, products=products, purchases=purchases)
        if not items:
            flash("Please add at least one item.", "danger")
            return render_template("supplier_returns/create.html",
                                   suppliers=suppliers, products=products, purchases=purchases)
        try:
            sr = supplier_return_service.create_supplier_return(
                supplier_id=int(supplier_id),
                items=items,
                user_id=current_user.id,
                purchase_id=int(purchase_id) if purchase_id else None,
                reason=reason,
                notes=notes,
            )
            flash(f"Supplier return {sr.reference} created successfully.", "success")
            return redirect(url_for("supplier_returns.list_returns"))
        except ValueError as e:
            flash(str(e), "danger")

    return render_template("supplier_returns/create.html",
                           suppliers=suppliers, products=products, purchases=purchases)


@supplier_returns_bp.route("/<int:return_id>")
@admin_required
def view_return(return_id):
    from ...models.supplier_return import SupplierReturn
    sr = db.get_or_404(SupplierReturn, return_id)
    return render_template("supplier_returns/detail.html", sr=sr)


@supplier_returns_bp.route("/<int:return_id>/status", methods=["POST"])
@admin_required
def update_status(return_id):
    status = request.form.get("status", "")
    try:
        supplier_return_service.update_status(return_id, status)
        flash(f"Status updated to '{status}'.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("supplier_returns.view_return", return_id=return_id))


def _parse_items(form) -> list[dict]:
    items = []
    idx = 0
    while True:
        pid = form.get(f"items[{idx}][product_id]")
        if pid is None:
            break
        try:
            qty = int(form.get(f"items[{idx}][quantity]", 0))
            cost = float(form.get(f"items[{idx}][unit_cost]", 0))
            item_reason = form.get(f"items[{idx}][reason]", "").strip() or None
            if int(pid) > 0 and qty > 0:
                items.append({"product_id": int(pid), "quantity": qty,
                              "unit_cost": cost, "reason": item_reason})
        except (ValueError, TypeError):
            pass
        idx += 1
    return items
