"""Inventory blueprint — product CRUD, stock adjustment, and category management."""

import os
import uuid

from flask import Blueprint, flash, redirect, render_template, request, url_for, current_app, abort
from flask_login import current_user

from ...extensions import db
from ...models.product import Product
from ...models.supplier import Supplier
from ...models.category import Category
from ...services import inventory_manager
from ...services.decorators import admin_required, login_required

inventory_bp = Blueprint("inventory", __name__, url_prefix="/inventory")

def _require_perm(perm: str):
    from flask import abort
    from flask_login import current_user as _cu
    if _cu.role != 'admin':
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(_cu.id)
        if not getattr(p, perm, False):
            abort(403)



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
    page = request.args.get("page", 1, type=int)
    products = inventory_manager.get_products(search=search, page=page)
    # Get total count for pagination
    from sqlalchemy import func as _func, or_
    stmt = db.select(_func.count(Product.id))
    if search:
        term = search.strip().lower()
        stmt = stmt.where(
            or_(
                db.func.lower(Product.name).contains(term),
                db.func.lower(Product.category).contains(term),
                db.func.lower(Product.sku) == term,
            )
        )
    total = db.session.execute(stmt).scalar() or 0
    per_page = 100
    total_pages = max(1, (total + per_page - 1) // per_page)
    return render_template("inventory/list.html", products=products, search=search or "",
                           page=page, total=total, total_pages=total_pages, per_page=per_page)


@inventory_bp.route("/create", methods=["GET", "POST"])
@login_required
def create_product():
    if current_user.role != "admin":
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(current_user.id)
        if not p.can_add_product:
            abort(403)
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
    if current_user.role != "admin":
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(current_user.id)
        if not p.can_edit_product:
            abort(403)
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
    if current_user.role != "admin":
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(current_user.id)
        if not p.can_adjust_stock:
            abort(403)
    product = db.get_or_404(Product, product_id)
    if request.method == "POST":
        direction = request.form.get("direction", "in")
        note = request.form.get("note", "").strip()
        adjustment_type = request.form.get("adjustment_type", "").strip() or None
        try:
            qty = int(request.form.get("quantity", 0))
            if qty <= 0:
                raise ValueError("Quantity must be a positive integer.")
            inventory_manager.adjust_stock(product_id, qty, direction, note, current_user.id,
                                           adjustment_type=adjustment_type)
            flash(f"Stock {'added' if direction == 'in' else 'removed'} successfully.", "success")
            return redirect(url_for("inventory.list_products"))
        except ValueError as e:
            flash(str(e), "danger")
    return render_template("inventory/adjust_stock.html", product=product)


# --- Category management (admin only) ---

@inventory_bp.route("/categories")
@login_required
def list_categories():
    _require_perm("can_manage_categories")
    from sqlalchemy import func, case
    from ...models.sale import Sale, SaleItem
    from datetime import date, timedelta

    # Base category list
    cats = db.session.execute(db.select(Category).order_by(Category.name)).scalars().all()

    # Build per-category stats in one pass
    # Product counts + stock per category
    product_stats = db.session.execute(
        db.select(
            Product.category,
            func.count(Product.id).label("product_count"),
            func.coalesce(func.sum(Product.quantity), 0).label("total_stock"),
            func.coalesce(func.sum(Product.quantity * Product.cost_price), 0).label("stock_value"),
            func.coalesce(func.sum(Product.quantity * Product.selling_price), 0).label("retail_value"),
            func.sum(case((Product.quantity == 0, 1), else_=0)).label("out_of_stock"),
            func.sum(case((Product.quantity <= 10, 1), else_=0)).label("low_stock"),
        )
        .group_by(Product.category)
    ).all()
    stats_map = {r.category or "": r for r in product_stats}

    # Revenue per category (last 30 days)
    thirty_days_ago = date.today() - timedelta(days=30)
    rev_rows = db.session.execute(
        db.select(
            Product.category,
            func.coalesce(func.sum(SaleItem.subtotal), 0).label("revenue"),
            func.coalesce(func.sum(SaleItem.quantity), 0).label("qty_sold"),
        )
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= thirty_days_ago)
        .group_by(Product.category)
    ).all()
    rev_map = {r.category or "": r for r in rev_rows}

    # Combine
    category_data = []
    for cat in cats:
        s = stats_map.get(cat.name, None)
        r = rev_map.get(cat.name, None)
        category_data.append({
            "cat": cat,
            "product_count": s.product_count if s else 0,
            "total_stock": int(s.total_stock) if s else 0,
            "stock_value": float(s.stock_value) if s else 0.0,
            "retail_value": float(s.retail_value) if s else 0.0,
            "out_of_stock": int(s.out_of_stock) if s else 0,
            "low_stock": int(s.low_stock) if s else 0,
            "revenue_30d": float(r.revenue) if r else 0.0,
            "qty_sold_30d": int(r.qty_sold) if r else 0,
        })

    # Summary totals
    total_products = sum(c["product_count"] for c in category_data)
    total_stock_value = sum(c["stock_value"] for c in category_data)
    total_revenue_30d = sum(c["revenue_30d"] for c in category_data)

    return render_template("inventory/categories.html",
                           category_data=category_data,
                           total_products=total_products,
                           total_stock_value=total_stock_value,
                           total_revenue_30d=total_revenue_30d)


@inventory_bp.route("/categories/<int:cat_id>")
@login_required
def category_detail(cat_id):
    _require_perm("can_manage_categories")
    from sqlalchemy import func, case
    from ...models.sale import Sale, SaleItem
    from ...models.stock_movement import StockMovement
    from datetime import date, timedelta

    cat = db.get_or_404(Category, cat_id)
    products = db.session.execute(
        db.select(Product).where(Product.category == cat.name).order_by(Product.name)
    ).scalars().all()

    # Per-product sales stats (last 30 days)
    thirty_days_ago = date.today() - timedelta(days=30)
    sales_stats = db.session.execute(
        db.select(
            SaleItem.product_id,
            func.coalesce(func.sum(SaleItem.subtotal), 0).label("revenue"),
            func.coalesce(func.sum(SaleItem.quantity), 0).label("qty_sold"),
            func.count(SaleItem.id.distinct()).label("txn_count"),
        )
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(
            SaleItem.product_id.in_([p.id for p in products]),
            func.date(Sale.sale_date) >= thirty_days_ago,
        )
        .group_by(SaleItem.product_id)
    ).all()
    sales_map = {r.product_id: r for r in sales_stats}

    # Category-level totals
    total_stock = sum(p.quantity for p in products)
    total_stock_value = sum(float(p.cost_price) * p.quantity for p in products)
    total_retail_value = sum(float(p.selling_price) * p.quantity for p in products)
    total_revenue_30d = sum(float(sales_map[p.id].revenue) if p.id in sales_map else 0 for p in products)
    total_qty_sold_30d = sum(int(sales_map[p.id].qty_sold) if p.id in sales_map else 0 for p in products)
    out_of_stock = sum(1 for p in products if p.quantity == 0)
    low_stock = sum(1 for p in products if 0 < p.quantity <= 10)

    # Daily revenue trend (last 14 days) for this category
    trend_rows = db.session.execute(
        db.select(
            func.date(Sale.sale_date).label("day"),
            func.coalesce(func.sum(SaleItem.subtotal), 0).label("revenue"),
        )
        .join(SaleItem, SaleItem.sale_id == Sale.id)
        .join(Product, Product.id == SaleItem.product_id)
        .where(
            Product.category == cat.name,
            func.date(Sale.sale_date) >= date.today() - timedelta(days=13),
        )
        .group_by(func.date(Sale.sale_date))
        .order_by(func.date(Sale.sale_date))
    ).all()
    trend_labels = [str(r.day) for r in trend_rows]
    trend_data = [float(r.revenue) for r in trend_rows]

    # Enrich products with their stats
    enriched = []
    for p in products:
        s = sales_map.get(p.id)
        enriched.append({
            "product": p,
            "revenue_30d": float(s.revenue) if s else 0.0,
            "qty_sold_30d": int(s.qty_sold) if s else 0,
            "txn_count": int(s.txn_count) if s else 0,
            "profit_30d": (float(s.revenue) - float(p.cost_price) * int(s.qty_sold)) if s else 0.0,
        })
    # Sort by revenue desc
    enriched.sort(key=lambda x: x["revenue_30d"], reverse=True)

    return render_template("inventory/category_detail.html",
                           cat=cat, enriched=enriched,
                           total_stock=total_stock,
                           total_stock_value=total_stock_value,
                           total_retail_value=total_retail_value,
                           total_revenue_30d=total_revenue_30d,
                           total_qty_sold_30d=total_qty_sold_30d,
                           out_of_stock=out_of_stock,
                           low_stock=low_stock,
                           trend_labels=trend_labels,
                           trend_data=trend_data,
                           today=date.today())


@inventory_bp.route("/categories/create", methods=["GET", "POST"])
@login_required
def create_category():
    _require_perm("can_manage_categories")
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
@login_required
def edit_category(cat_id):
    _require_perm("can_manage_categories")
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
@login_required
def delete_category(cat_id):
    _require_perm("can_manage_categories")
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

            # Resolve supplier — create if not found
            supplier_id = None
            if supplier_name:
                from ...models.supplier import Supplier
                sup = db.session.execute(
                    db.select(Supplier).filter(
                        db.func.lower(Supplier.name) == supplier_name.lower()
                    )
                ).scalar_one_or_none()
                if not sup:
                    # Auto-create supplier
                    sup = Supplier(name=supplier_name.strip())
                    db.session.add(sup)
                    db.session.flush()
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
                # Create new product directly (no internal commit — we commit at end)
                try:
                    from sqlalchemy.exc import IntegrityError
                    p_obj = Product(
                        name=name, category=cat_val, sku=sku,
                        cost_price=cost, selling_price=sell,
                        quantity=qty, unit=unit,
                        supplier_id=supplier_id, expiry_date=expiry_date,
                    )
                    db.session.add(p_obj)
                    db.session.flush()  # catch duplicate SKU immediately
                    created += 1
                except Exception as e:
                    db.session.rollback()
                    errors.append(f"Row {row_num}: '{name}' skipped — {e}")
                    skipped += 1
                    continue

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash(f"Error saving data: {e}", "danger")
            return render_template("inventory/bulk_upload.html")

        flash(f"✅ Bulk upload complete — {created} created, {updated} updated, {skipped} skipped.", "success")
        for err in errors:
            flash(err, "warning")
        return redirect(url_for("inventory.list_products"))

    return render_template("inventory/bulk_upload.html")


@inventory_bp.route("/export-csv")
@admin_required
def export_csv():
    """Export full product list as CSV."""
    import csv, io
    from flask import Response
    products = db.session.execute(db.select(Product).order_by(Product.name)).scalars().all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name", "SKU", "Category", "Cost Price", "Selling Price",
                     "Quantity", "Unit", "Supplier", "Expiry Date"])
    for p in products:
        writer.writerow([
            p.name, p.sku, p.category or "",
            float(p.cost_price), float(p.selling_price),
            p.quantity, p.unit or "pcs",
            p.supplier.name if p.supplier else "",
            p.expiry_date.isoformat() if p.expiry_date else "",
        ])
    return Response(
        output.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=inventory.csv"}
    )


@inventory_bp.route("/labels", methods=["GET", "POST"])
@login_required
def print_labels():
    """Generate printable barcode/price labels for products."""
    products = db.session.execute(db.select(Product).order_by(Product.name)).scalars().all()
    selected_ids = []
    if request.method == "POST":
        selected_ids = [int(x) for x in request.form.getlist("product_ids") if x.isdigit()]
    return render_template("inventory/labels.html", products=products, selected_ids=selected_ids)


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


# ── Product Variants ─────────────────────────────────────────────────────────

@inventory_bp.route("/<int:product_id>/variants")
@login_required
def product_variants(product_id):
    _require_perm("can_manage_variants")
    product = db.get_or_404(Product, product_id)
    from ...models.product_variant import ProductVariant
    variants = db.session.execute(
        db.select(ProductVariant).where(ProductVariant.product_id == product_id)
        .order_by(ProductVariant.variant_name)
    ).scalars().all()
    return render_template("inventory/variants.html", product=product, variants=variants)


@inventory_bp.route("/<int:product_id>/variants/create", methods=["GET", "POST"])
@login_required
def create_variant(product_id):
    _require_perm("can_manage_variants")
    product = db.get_or_404(Product, product_id)
    from ...models.product_variant import ProductVariant
    from sqlalchemy.exc import IntegrityError
    if request.method == "POST":
        variant_name = request.form.get("variant_name", "").strip()
        sku = request.form.get("sku", "").strip()
        cost_price = float(request.form.get("cost_price", 0) or 0)
        selling_price = float(request.form.get("selling_price", 0) or 0)
        quantity = int(request.form.get("quantity", 0) or 0)
        barcode = request.form.get("barcode", "").strip() or None
        if not variant_name or not sku:
            flash("Variant name and SKU are required.", "danger")
        else:
            try:
                v = ProductVariant(
                    product_id=product_id,
                    variant_name=variant_name,
                    sku=sku,
                    cost_price=cost_price,
                    selling_price=selling_price,
                    quantity=quantity,
                    barcode=barcode,
                    is_active=True,
                )
                db.session.add(v)
                db.session.commit()
                flash(f"Variant '{variant_name}' added.", "success")
                return redirect(url_for("inventory.product_variants", product_id=product_id))
            except IntegrityError:
                db.session.rollback()
                flash(f"SKU '{sku}' already exists.", "danger")
    return render_template("inventory/variant_form.html", product=product, variant=None, action="Add")


@inventory_bp.route("/<int:product_id>/variants/<int:variant_id>/edit", methods=["GET", "POST"])
@login_required
def edit_variant(product_id, variant_id):
    _require_perm("can_manage_variants")
    product = db.get_or_404(Product, product_id)
    from ...models.product_variant import ProductVariant
    from sqlalchemy.exc import IntegrityError
    variant = db.get_or_404(ProductVariant, variant_id)
    if request.method == "POST":
        variant.variant_name = request.form.get("variant_name", "").strip()
        variant.sku = request.form.get("sku", "").strip()
        variant.cost_price = float(request.form.get("cost_price", 0) or 0)
        variant.selling_price = float(request.form.get("selling_price", 0) or 0)
        variant.quantity = int(request.form.get("quantity", 0) or 0)
        variant.barcode = request.form.get("barcode", "").strip() or None
        variant.is_active = request.form.get("is_active") == "on"
        try:
            db.session.commit()
            flash("Variant updated.", "success")
            return redirect(url_for("inventory.product_variants", product_id=product_id))
        except IntegrityError:
            db.session.rollback()
            flash("SKU already exists.", "danger")
    return render_template("inventory/variant_form.html", product=product, variant=variant, action="Edit")


@inventory_bp.route("/<int:product_id>/variants/<int:variant_id>/delete", methods=["POST"])
@login_required
def delete_variant(product_id, variant_id):
    _require_perm("can_manage_variants")
    from ...models.product_variant import ProductVariant
    v = db.get_or_404(ProductVariant, variant_id)
    db.session.delete(v)
    db.session.commit()
    flash("Variant deleted.", "success")
    return redirect(url_for("inventory.product_variants", product_id=product_id))


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
