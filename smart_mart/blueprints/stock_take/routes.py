"""Stock Take blueprint — physical inventory count (Feature #3)."""
from __future__ import annotations

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from ...extensions import db
from ...models.product import Product
from ...models.stock_take import StockTake
from ...services import stock_take_service
from ...services.decorators import admin_required, login_required

stock_take_bp = Blueprint("stock_take", __name__, url_prefix="/stock-take")


@stock_take_bp.route("/")
@admin_required
def list_takes():
    page = request.args.get("page", 1, type=int)
    takes = stock_take_service.list_stock_takes(page=page)
    return render_template("stock_take/list.html", takes=takes, page=page)


@stock_take_bp.route("/create", methods=["GET", "POST"])
@admin_required
def create_take():
    if request.method == "POST":
        notes = request.form.get("notes", "").strip() or None
        scope = request.form.get("scope", "all")
        product_ids = None
        if scope == "selected":
            product_ids = [int(x) for x in request.form.getlist("product_ids") if x.isdigit()]
            if not product_ids:
                flash("Please select at least one product.", "danger")
                products = db.session.execute(db.select(Product).order_by(Product.name)).scalars().all()
                return render_template("stock_take/create.html", products=products)
        try:
            take = stock_take_service.create_stock_take(
                user_id=current_user.id, notes=notes, product_ids=product_ids
            )
            flash(f"Stock take {take.reference} started.", "success")
            return redirect(url_for("stock_take.count", take_id=take.id))
        except Exception as e:
            flash(str(e), "danger")
    products = db.session.execute(db.select(Product).order_by(Product.name)).scalars().all()
    return render_template("stock_take/create.html", products=products)


@stock_take_bp.route("/<int:take_id>/count", methods=["GET", "POST"])
@admin_required
def count(take_id):
    take = db.get_or_404(StockTake, take_id)
    if take.status not in ("draft", "in_progress"):
        flash("This stock take is already completed or cancelled.", "warning")
        return redirect(url_for("stock_take.view", take_id=take_id))

    if request.method == "POST":
        counts = {}
        for item in take.items:
            raw = request.form.get(f"count_{item.product_id}", "").strip()
            if raw != "":
                try:
                    counts[item.product_id] = int(raw)
                except ValueError:
                    pass
        stock_take_service.update_counts(take_id, counts)
        flash("Counts saved.", "success")
        return redirect(url_for("stock_take.count", take_id=take_id))

    return render_template("stock_take/count.html", take=take)


@stock_take_bp.route("/<int:take_id>/complete", methods=["POST"])
@admin_required
def complete(take_id):
    apply = request.form.get("apply_adjustments") == "1"
    try:
        take = stock_take_service.complete_stock_take(take_id, current_user.id, apply)
        msg = f"Stock take {take.reference} completed."
        if apply:
            variances = len(take.items_with_variance)
            msg += f" {variances} product(s) adjusted."
        flash(msg, "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("stock_take.view", take_id=take_id))


@stock_take_bp.route("/<int:take_id>/cancel", methods=["POST"])
@admin_required
def cancel(take_id):
    try:
        stock_take_service.cancel_stock_take(take_id)
        flash("Stock take cancelled.", "warning")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("stock_take.list_takes"))


@stock_take_bp.route("/<int:take_id>")
@admin_required
def view(take_id):
    take = db.get_or_404(StockTake, take_id)
    return render_template("stock_take/view.html", take=take)


@stock_take_bp.route("/<int:take_id>/api/save-count", methods=["POST"])
@admin_required
def api_save_count(take_id):
    """AJAX endpoint to save a single product count."""
    data = request.get_json() or {}
    product_id = data.get("product_id")
    counted_qty = data.get("counted_qty")
    if product_id is None or counted_qty is None:
        return jsonify({"ok": False, "error": "Missing fields"}), 400
    try:
        stock_take_service.update_counts(take_id, {int(product_id): int(counted_qty)})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
