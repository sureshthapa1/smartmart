"""Inventory blueprint — product CRUD, stock adjustment, and category management."""

import os
import uuid

from flask import Blueprint, flash, redirect, render_template, request, url_for, current_app
from flask_login import current_user

from ...extensions import db
from ...models.product import Product
from ...models.supplier import Supplier
from ...models.category import Category
from ...services import inventory_manager
from ...services.decorators import admin_required, login_required

inventory_bp = Blueprint("inventory", __name__, url_prefix="/inventory")

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}


def _allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _save_product_image(file) -> str | None:
    """Save uploaded image and return the filename."""
    if not file or file.filename == "":
        return None
    if not _allowed_file(file.filename):
        return None
    ext = file.filename.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    upload_dir = os.path.join(current_app.static_folder, "uploads", "products")
    os.makedirs(upload_dir, exist_ok=True)
    file.save(os.path.join(upload_dir, filename))
    return filename


def _delete_product_image(filename: str) -> None:
    """Delete an existing product image file."""
    if filename:
        try:
            path = os.path.join(current_app.static_folder, "uploads", "products", filename)
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


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
        # Handle image upload (admin only)
        if current_user.role == "admin":
            img_file = request.files.get("product_image")
            img_filename = _save_product_image(img_file)
            if img_filename:
                data["image_filename"] = img_filename
            # Save custom emoji to ProductIconMap
            custom_emoji = request.form.get("custom_emoji", "").strip()
            if custom_emoji and data.get("name"):
                try:
                    from ...models.product_icon_map import ProductIconMap
                    ProductIconMap.set(data["name"], custom_emoji)
                except Exception:
                    pass
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
        # Handle image upload (admin only)
        if current_user.role == "admin":
            img_file = request.files.get("product_image")
            img_filename = _save_product_image(img_file)
            if img_filename:
                data["image_filename"] = img_filename
            # Handle image removal
            if request.form.get("remove_image") == "1":
                _delete_product_image(product.image_filename)
                data["image_filename"] = None
            # Save custom emoji to ProductIconMap
            custom_emoji = request.form.get("custom_emoji", "").strip()
            if custom_emoji and data.get("name"):
                try:
                    from ...models.product_icon_map import ProductIconMap
                    ProductIconMap.set(data["name"], custom_emoji)
                except Exception:
                    pass
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


# ── Bulk Product Upload ──────────────────────────────────────────────────

@inventory_bp.route("/bulk-upload", methods=["GET", "POST"])
@admin_required
def bulk_upload():
    """Bulk product upload via CSV or Excel."""
    import io, csv, uuid as _uuid

    if request.method == "POST":
        file = request.files.get("file")
        if not file or file.filename == "":
            flash("Please select a CSV or Excel file.", "danger")
            return render_template("inventory/bulk_upload.html")

        filename = file.filename.lower()
        rows = []
        try:
            if filename.endswith(".csv"):
                content = file.read().decode("utf-8-sig")
                reader = csv.DictReader(io.StringIO(content))
                for i, row in enumerate(reader, 2):
                    rows.append((i, row))
            elif filename.endswith((".xlsx", ".xls")):
                import openpyxl
                wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
                ws = wb.active
                headers = [str(c.value).strip().lower() if c.value else ""
                           for c in next(ws.iter_rows(min_row=1, max_row=1))]
                for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
                    rows.append((i, dict(zip(headers, row))))
            else:
                flash("Only .csv or .xlsx files are supported.", "danger")
                return render_template("inventory/bulk_upload.html")
        except Exception as e:
            flash(f"Error reading file: {e}", "danger")
            return render_template("inventory/bulk_upload.html")

        created = updated = skipped = 0
        errors = []

        for row_num, row in rows:
            r = {k.strip().lower(): str(v).strip() if v is not None else ""
                 for k, v in row.items()}

            name = r.get("product name") or r.get("name") or r.get("product") or ""
            sku = r.get("sku") or r.get("barcode") or ""
            category = r.get("category") or ""
            cost_raw = r.get("cost price") or r.get("cost") or r.get("cost_price") or "0"
            sell_raw = r.get("selling price") or r.get("price") or r.get("selling_price") or "0"
            qty_raw = r.get("quantity") or r.get("qty") or "0"
            unit = r.get("unit") or "pcs"
            supplier_name = r.get("supplier") or ""
            expiry_raw = r.get("expiry date") or r.get("expiry") or ""

            if not name:
                errors.append(f"Row {row_num}: missing product name, skipped.")
                skipped += 1
                continue

            try:
                cost = float(cost_raw) if cost_raw else 0.0
                sell = float(sell_raw) if sell_raw else cost
                qty = int(float(qty_raw)) if qty_raw else 0
            except ValueError:
                errors.append(f"Row {row_num}: invalid number for '{name}', skipped.")
                skipped += 1
                continue

            # Auto-generate SKU if missing
            if not sku:
                sku = f"{name[:4].upper().replace(' ', '')}-{_uuid.uuid4().hex[:4].upper()}"

            # Resolve supplier
            supplier_id = None
            if supplier_name:
                from ...models.supplier import Supplier
                sup = db.session.execute(
                    db.select(Supplier).filter(
                        db.func.lower(Supplier.name) == supplier_name.lower()
                    )
                ).scalar_one_or_none()
                if sup:
                    supplier_id = sup.id

            # Resolve/create category
            cat_val = category or None
            if category:
                existing_cat = db.session.execute(
                    db.select(Category).filter_by(name=category)
                ).scalar_one_or_none()
                if not existing_cat:
                    new_cat = Category(name=category)
                    db.session.add(new_cat)
                    db.session.flush()

            # Parse expiry
            expiry_date = None
            if expiry_raw:
                from datetime import date as _date
                for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
                    try:
                        expiry_date = _date.fromisoformat(expiry_raw) if fmt == "%Y-%m-%d" \
                            else _date.strptime(expiry_raw, fmt)
                        break
                    except ValueError:
                        continue

            # Check if product exists by SKU
            existing = db.session.execute(
                db.select(Product).filter_by(sku=sku)
            ).scalar_one_or_none()

            if existing:
                # Update existing product
                existing.name = name
                existing.category = cat_val
                existing.cost_price = cost
                existing.selling_price = sell
                existing.quantity = qty
                existing.unit = unit
                existing.supplier_id = supplier_id
                if expiry_date:
                    existing.expiry_date = expiry_date
                updated += 1
            else:
                # Create new product
                try:
                    from ...services import inventory_manager
                    inventory_manager.create_product({
                        "name": name, "category": cat_val, "sku": sku,
                        "cost_price": cost, "selling_price": sell,
                        "quantity": qty, "unit": unit,
                        "supplier_id": supplier_id, "expiry_date": expiry_date,
                    })
                    created += 1
                except ValueError as e:
                    errors.append(f"Row {row_num}: {e}")
                    skipped += 1
                    continue

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash(f"Error saving data: {e}", "danger")
            return render_template("inventory/bulk_upload.html")

        flash(f"✅ Bulk upload complete — {created} created, {updated} updated, {skipped} skipped.", "success")
        for err in errors[:5]:
            flash(err, "warning")
        return redirect(url_for("inventory.list_products"))

    return render_template("inventory/bulk_upload.html")


@inventory_bp.route("/bulk-upload/sample")
@admin_required
def download_product_sample():
    """Download a sample CSV for bulk product upload."""
    from flask import Response
    sample = "Product Name,SKU,Category,Cost Price,Selling Price,Quantity,Unit,Supplier,Expiry Date\n"
    sample += "Basmati Rice,RICE-001,Grains & Pulses,80.00,120.00,50,kg,ABC Traders,2026-12-31\n"
    sample += "Mustard Oil 1L,OIL-001,Oils & Fats,150.00,200.00,30,pcs,XYZ Suppliers,\n"
    sample += "Colgate Toothpaste,TOOTH-001,Personal Care & Hygiene,75.00,110.00,20,pcs,,2027-06-30\n"
    sample += "Wai Wai Noodles,NOODLE-001,Snacks & Bakery,18.00,25.00,100,pcs,,\n"
    return Response(
        sample, mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=bulk_products_sample.csv"}
    )


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
        "unit": form.get("unit", "pcs").strip() or "pcs",
    }
    expiry_raw = form.get("expiry_date", "").strip()
    if expiry_raw:
        try:
            data["expiry_date"] = date.fromisoformat(expiry_raw)
        except ValueError:
            pass
    return data
