"""Purchase Orders blueprint."""
from datetime import date
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from ...extensions import db
from ...models.product import Product
from ...models.supplier import Supplier
from ...services import po_manager
from ...services.decorators import admin_required, login_required

po_bp = Blueprint("purchase_orders", __name__, url_prefix="/purchase-orders")


@po_bp.route("/")
@admin_required
def list_pos():
    status = request.args.get("status", "")
    pos = po_manager.list_pos(status or None)
    return render_template("purchase_orders/list.html", pos=pos, status_filter=status)


@po_bp.route("/create", methods=["GET", "POST"])
@admin_required
def create_po():
    suppliers = db.session.execute(db.select(Supplier).order_by(Supplier.name)).scalars().all()
    products = db.session.execute(db.select(Product).order_by(Product.name)).scalars().all()
    if request.method == "POST":
        supplier_id = request.form.get("supplier_id", "")
        expected_raw = request.form.get("expected_date", "")
        notes = request.form.get("notes", "").strip() or None
        items = []
        idx = 0
        while True:
            pid = request.form.get(f"items[{idx}][product_id]")
            if pid is None:
                break
            try:
                qty = int(request.form.get(f"items[{idx}][quantity]", 0))
                cost = float(request.form.get(f"items[{idx}][unit_cost]", 0))
                if int(pid) > 0 and qty > 0:
                    items.append({"product_id": int(pid), "quantity": qty, "unit_cost": cost})
            except (ValueError, TypeError):
                pass
            idx += 1
        if not supplier_id or not items:
            flash("Supplier and at least one item are required.", "danger")
            return render_template("purchase_orders/form.html", suppliers=suppliers, products=products, po=None)
        try:
            expected = date.fromisoformat(expected_raw) if expected_raw else None
            po = po_manager.create_po(int(supplier_id), items, current_user.id, expected, notes)
            flash(f"Purchase Order {po.po_number} created.", "success")
            return redirect(url_for("purchase_orders.po_detail", po_id=po.id))
        except ValueError as e:
            flash(str(e), "danger")
    return render_template("purchase_orders/form.html", suppliers=suppliers, products=products, po=None)


@po_bp.route("/<int:po_id>")
@admin_required
def po_detail(po_id):
    from ...models.purchase_order import PurchaseOrder
    po = db.get_or_404(PurchaseOrder, po_id)
    return render_template("purchase_orders/detail.html", po=po)


@po_bp.route("/<int:po_id>/send", methods=["POST"])
@admin_required
def send_po(po_id):
    try:
        po = po_manager.send_po(po_id)
        flash(f"PO {po.po_number} marked as sent.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("purchase_orders.po_detail", po_id=po_id))


@po_bp.route("/<int:po_id>/receive", methods=["POST"])
@admin_required
def receive_po(po_id):
    from ...models.purchase_order import PurchaseOrder, PurchaseOrderItem
    po = db.get_or_404(PurchaseOrder, po_id)
    received = {}
    for item in po.items:
        qty_raw = request.form.get(f"qty_{item.id}", "0")
        try:
            received[item.id] = max(0, int(qty_raw))
        except ValueError:
            received[item.id] = 0
    try:
        po = po_manager.receive_po(po_id, current_user.id, received)
        flash(f"PO {po.po_number} updated — status: {po.status}.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("purchase_orders.po_detail", po_id=po_id))


@po_bp.route("/<int:po_id>/cancel", methods=["POST"])
@admin_required
def cancel_po(po_id):
    try:
        po = po_manager.cancel_po(po_id)
        flash(f"PO {po.po_number} cancelled.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("purchase_orders.list_pos"))
