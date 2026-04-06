"""Purchases blueprint — purchase creation, listing, and supplier management."""

from __future__ import annotations

from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ...extensions import db
from ...models.product import Product
from ...services import purchase_manager
from ...services.decorators import login_required, admin_required

purchases_bp = Blueprint("purchases", __name__, url_prefix="/purchases")


@purchases_bp.route("/")
@login_required
def list_purchases():
    start_date_raw = request.args.get("start_date", "").strip() or None
    end_date_raw = request.args.get("end_date", "").strip() or None
    filters: dict = {}
    if start_date_raw:
        try:
            filters["start_date"] = date.fromisoformat(start_date_raw)
        except ValueError:
            flash("Invalid start date format.", "danger")
    if end_date_raw:
        try:
            filters["end_date"] = date.fromisoformat(end_date_raw)
        except ValueError:
            flash("Invalid end date format.", "danger")
    page = int(request.args.get("page", 1))
    purchases = purchase_manager.list_purchases(filters, page=page)
    return render_template("purchases/list.html", purchases=purchases,
                           start_date=start_date_raw or "", end_date=end_date_raw or "")


@purchases_bp.route("/create", methods=["GET", "POST"])
@login_required
def create_purchase():
    suppliers = purchase_manager.list_suppliers()
    products = db.session.execute(db.select(Product).order_by(Product.name)).scalars().all()
    if request.method == "POST":
        supplier_id_raw = request.form.get("supplier_id", "").strip()
        purchase_date_raw = request.form.get("purchase_date", "").strip()
        items = _parse_items(request.form)
        errors = []
        if not supplier_id_raw:
            errors.append("Please select a supplier.")
        if not purchase_date_raw:
            errors.append("Please provide a purchase date.")
        if not items:
            errors.append("Please add at least one item.")
        purchase_date = None
        if purchase_date_raw:
            try:
                purchase_date = date.fromisoformat(purchase_date_raw)
            except ValueError:
                errors.append("Invalid purchase date format.")
        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template("purchases/create.html", suppliers=suppliers, products=products,
                                   today=date.today().isoformat())
        try:
            purchase = purchase_manager.create_purchase(
                supplier_id=int(supplier_id_raw), items=items,
                purchase_date=purchase_date, user_id=current_user.id,
            )
            flash(f"Purchase #{purchase.id} created successfully.", "success")
            return redirect(url_for("purchases.list_purchases"))
        except ValueError as e:
            flash(str(e), "danger")
        except Exception as e:
            flash(f"Error creating purchase: {e}", "danger")
    return render_template("purchases/create.html", suppliers=suppliers, products=products,
                           today=date.today().isoformat())


@purchases_bp.route("/suppliers")
@login_required
def list_suppliers():
    suppliers = purchase_manager.list_suppliers()
    return render_template("purchases/suppliers.html", suppliers=suppliers)


@purchases_bp.route("/suppliers/create", methods=["GET", "POST"])
@admin_required
def create_supplier():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        contact = request.form.get("contact", "").strip()
        email = request.form.get("email", "").strip()
        address = request.form.get("address", "").strip()
        if not name:
            flash("Supplier name is required.", "danger")
            return render_template("purchases/supplier_form.html", supplier=None)
        try:
            supplier = purchase_manager.create_supplier({
                "name": name, "contact": contact or None,
                "email": email or None, "address": address or None,
            })
            flash(f"Supplier '{supplier.name}' created successfully.", "success")
            return redirect(url_for("purchases.list_suppliers"))
        except Exception as e:
            flash(f"Error creating supplier: {e}", "danger")
    return render_template("purchases/supplier_form.html", supplier=None)


@purchases_bp.route("/suppliers/<int:supplier_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_supplier(supplier_id):
    from ...models.supplier import Supplier
    from ...extensions import db as _db
    supplier = _db.get_or_404(Supplier, supplier_id)
    if request.method == "POST":
        data = {
            "name": request.form.get("name", "").strip(),
            "contact": request.form.get("contact", "").strip() or None,
            "email": request.form.get("email", "").strip() or None,
            "address": request.form.get("address", "").strip() or None,
        }
        try:
            purchase_manager.update_supplier(supplier_id, data)
            flash("Supplier updated successfully.", "success")
            return redirect(url_for("purchases.list_suppliers"))
        except Exception as e:
            flash(str(e), "danger")
    return render_template("purchases/supplier_form.html", supplier=supplier)


@purchases_bp.route("/suppliers/<int:supplier_id>/delete", methods=["POST"])
@admin_required
def delete_supplier(supplier_id):
    try:
        purchase_manager.delete_supplier(supplier_id)
        flash("Supplier deleted.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("purchases.list_suppliers"))


def _parse_items(form) -> list[dict]:
    items: list[dict] = []
    index = 0
    while True:
        product_id = form.get(f"items[{index}][product_id]")
        if product_id is None:
            break
        try:
            pid = int(product_id)
            qty = int(form.get(f"items[{index}][quantity]", "0"))
            cost = float(form.get(f"items[{index}][unit_cost]", "0"))
            if pid > 0 and qty > 0 and cost >= 0:
                items.append({"product_id": pid, "quantity": qty, "unit_cost": cost})
        except (ValueError, TypeError):
            pass
        index += 1
    return items
