"""Inventory blueprint — product CRUD, stock adjustment, and category management."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ...extensions import db
from ...models.product import Product
from ...models.supplier import Supplier
from ...models.category import Category
from ...services import inventory_manager
from ...services.decorators import admin_required, login_required

inventory_bp = Blueprint("inventory", __name__, url_prefix="/inventory")


@inventory_bp.route("/")
@login_required
def list_products():
    search = request.args.get("q", "").strip() or None
    products = inventory_manager.get_products(search=search)
    return render_template("inventory/list.html", products=products, search=search or "")


@inventory_bp.route("/create", methods=["GET", "POST"])
@login_required
def create_product():
    suppliers = db.session.execute(db.select(Supplier).order_by(Supplier.name)).scalars().all()
    try:
        categories = db.session.execute(db.select(Category).order_by(Category.name)).scalars().all()
    except Exception:
        categories = []
    if request.method == "POST":
        data = _form_to_data(request.form)
        try:
            inventory_manager.create_product(data)
            flash("Product created successfully.", "success")
            return redirect(url_for("inventory.list_products"))
        except ValueError as e:
            flash(str(e), "danger")
    return render_template("inventory/form.html", product=None, suppliers=suppliers,
                           categories=categories, action="Create")


@inventory_bp.route("/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
def edit_product(product_id):
    product = db.get_or_404(Product, product_id)
    suppliers = db.session.execute(db.select(Supplier).order_by(Supplier.name)).scalars().all()
    try:
        categories = db.session.execute(db.select(Category).order_by(Category.name)).scalars().all()
    except Exception:
        categories = []
    if request.method == "POST":
        data = _form_to_data(request.form)
        try:
            inventory_manager.update_product(product_id, data)
            flash("Product updated successfully.", "success")
            return redirect(url_for("inventory.list_products"))
        except ValueError as e:
            flash(str(e), "danger")
    return render_template("inventory/form.html", product=product, suppliers=suppliers,
                           categories=categories, action="Edit")


@inventory_bp.route("/<int:product_id>/delete", methods=["POST"])
@admin_required
def delete_product(product_id):
    try:
        inventory_manager.delete_product(product_id)
        flash("Product deleted successfully.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("inventory.list_products"))


@inventory_bp.route("/<int:product_id>/adjust-stock", methods=["GET", "POST"])
@login_required
def adjust_stock(product_id):
    product = db.get_or_404(Product, product_id)
    if request.method == "POST":
        direction = request.form.get("direction", "in")
        note = request.form.get("note", "").strip()
        try:
            qty = int(request.form.get("quantity", 0))
            if qty <= 0:
                raise ValueError("Quantity must be a positive integer.")
            inventory_manager.adjust_stock(product_id, qty, direction, note, current_user.id)
            flash(f"Stock {'added' if direction == 'in' else 'removed'} successfully.", "success")
            return redirect(url_for("inventory.list_products"))
        except ValueError as e:
            flash(str(e), "danger")
    return render_template("inventory/adjust_stock.html", product=product)


# --- Category management (admin only) ---

@inventory_bp.route("/categories")
@admin_required
def list_categories():
    cats = db.session.execute(db.select(Category).order_by(Category.name)).scalars().all()
    return render_template("inventory/categories.html", categories=cats)


@inventory_bp.route("/categories/create", methods=["GET", "POST"])
@admin_required
def create_category():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Category name is required.", "danger")
            return render_template("inventory/category_form.html", category=None)
        existing = db.session.execute(
            db.select(Category).filter_by(name=name)
        ).scalar_one_or_none()
        if existing:
            flash(f"Category '{name}' already exists.", "danger")
            return render_template("inventory/category_form.html", category=None)
        cat = Category(name=name)
        db.session.add(cat)
        db.session.commit()
        flash(f"Category '{name}' created.", "success")
        return redirect(url_for("inventory.list_categories"))
    return render_template("inventory/category_form.html", category=None)


@inventory_bp.route("/categories/<int:cat_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_category(cat_id):
    cat = db.get_or_404(Category, cat_id)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Category name is required.", "danger")
        else:
            cat.name = name
            db.session.commit()
            flash("Category updated.", "success")
            return redirect(url_for("inventory.list_categories"))
    return render_template("inventory/category_form.html", category=cat)


@inventory_bp.route("/categories/<int:cat_id>/delete", methods=["POST"])
@admin_required
def delete_category(cat_id):
    cat = db.get_or_404(Category, cat_id)
    db.session.delete(cat)
    db.session.commit()
    flash(f"Category '{cat.name}' deleted.", "success")
    return redirect(url_for("inventory.list_categories"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _form_to_data(form) -> dict:
    from datetime import date

    # Handle category: use selected or create new one
    category_val = form.get("category", "").strip() or None
    new_cat = form.get("new_category", "").strip()
    if new_cat:
        try:
            existing = db.session.execute(
                db.select(Category).filter_by(name=new_cat)
            ).scalar_one_or_none()
            if not existing:
                cat = Category(name=new_cat)
                db.session.add(cat)
                db.session.commit()
        except Exception:
            db.session.rollback()
        category_val = new_cat
    elif category_val == "__new__":
        category_val = None

    data: dict = {
        "name": form.get("name", "").strip(),
        "category": category_val,
        "sku": form.get("sku", "").strip(),
        "cost_price": form.get("cost_price", "0") or "0",
        "selling_price": form.get("selling_price", "0") or "0",
        "quantity": int(form.get("quantity", 0) or 0),
        "supplier_id": int(form.get("supplier_id")) if form.get("supplier_id") else None,
        "expiry_date": None,
    }
    expiry_raw = form.get("expiry_date", "").strip()
    if expiry_raw:
        try:
            data["expiry_date"] = date.fromisoformat(expiry_raw)
        except ValueError:
            pass
    return data
