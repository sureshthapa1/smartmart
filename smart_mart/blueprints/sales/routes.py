"""Sales blueprint — POS-style sale creation, listing, detail, and invoice routes."""

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ...extensions import db
from ...models.product import Product
from ...services import sales_manager
from ...services.sales_manager import InsufficientStockError
from ...services.decorators import login_required, admin_required

sales_bp = Blueprint("sales", __name__, url_prefix="/sales")


@sales_bp.route("/")
@login_required
def list_sales():
    start_date = request.args.get("start_date", "").strip() or None
    end_date = request.args.get("end_date", "").strip() or None

    filters: dict = {}
    if start_date:
        try:
            filters["start_date"] = datetime.fromisoformat(start_date)
        except ValueError:
            flash("Invalid start date format.", "danger")
    if end_date:
        try:
            filters["end_date"] = datetime.fromisoformat(end_date)
        except ValueError:
            flash("Invalid end date format.", "danger")

    page = int(request.args.get("page", 1))
    sales = sales_manager.list_sales(filters, page=page)
    return render_template(
        "sales/list.html",
        sales=sales,
        start_date=start_date or "",
        end_date=end_date or "",
    )


@sales_bp.route("/create", methods=["GET", "POST"])
@login_required
def create_sale():
    products = db.session.execute(
        db.select(Product).order_by(Product.name)
    ).scalars().all()

    if request.method == "POST":
        items = _parse_items(request.form)
        if not items:
            flash("Please add at least one item to the sale.", "danger")
            return render_template("sales/create.html", products=products)

        try:
            sale = sales_manager.create_sale(
                items,
                user_id=current_user.id,
                customer_name=request.form.get("customer_name", "").strip() or None,
                customer_address=request.form.get("customer_address", "").strip() or None,
                customer_phone=request.form.get("customer_phone", "").strip() or None,
                payment_mode=request.form.get("payment_mode", "cash"),
                discount_amount=float(request.form.get("discount_amount", 0) or 0),
                discount_note=request.form.get("discount_note", "").strip() or None,
            )
            flash(f"Sale #{sale.id} created successfully.", "success")
            return redirect(url_for("sales.sale_detail", sale_id=sale.id))
        except InsufficientStockError as e:
            flash(str(e), "danger")
        except ValueError as e:
            flash(str(e), "danger")

    return render_template("sales/create.html", products=products)


@sales_bp.route("/<int:sale_id>")
@login_required
def sale_detail(sale_id):
    sale = sales_manager.get_sale(sale_id)
    return render_template("sales/detail.html", sale=sale)


@sales_bp.route("/<int:sale_id>/invoice")
@login_required
def download_invoice(sale_id):
    pdf_bytes = sales_manager.generate_invoice_pdf(sale_id)
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=invoice_{sale_id}.pdf"},
    )


@sales_bp.route("/<int:sale_id>/delete", methods=["POST"])
@admin_required
def delete_sale(sale_id):
    try:
        sales_manager.delete_sale(sale_id)
        flash(f"Sale #{sale_id} has been deleted and stock reversed.", "success")
    except Exception as e:
        flash(f"Error deleting sale: {e}", "danger")
    return redirect(url_for("sales.list_sales"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_items(form) -> list[dict]:
    """Parse items[N][product_id/quantity/unit_price] fields from form data."""
    items: list[dict] = []
    index = 0
    while True:
        product_id = form.get(f"items[{index}][product_id]")
        if product_id is None:
            break
        quantity_raw = form.get(f"items[{index}][quantity]", "0")
        unit_price_raw = form.get(f"items[{index}][unit_price]", "0")
        try:
            pid = int(product_id)
            qty = int(quantity_raw)
            price = float(unit_price_raw)
            if pid > 0 and qty > 0 and price >= 0:
                items.append({"product_id": pid, "quantity": qty, "unit_price": price})
        except (ValueError, TypeError):
            pass
        index += 1
    return items
