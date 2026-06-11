# smart_mart/blueprints/bundles/routes.py
# ==========================================
# Gift bundle management: create, edit, sell, stock check.

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, jsonify,
)
from flask_login import login_required, current_user
from smart_mart.extensions import db
from smart_mart.models.bundle import Bundle, BundleItem
from smart_mart.models.product import Product
from smart_mart.models.stock_movement import StockMovement  # adjust to your model
import datetime

bundles_bp = Blueprint(
    "bundles", __name__,
    url_prefix="/bundles",
    template_folder="../../templates/bundles",
)

SEASON_CHOICES = ["Dashain", "Tihar", "Wedding", "New Year", "General"]


# ── List ──────────────────────────────────────────────────────────────────────
@bundles_bp.route("/")
@login_required
def index():
    bundles = Bundle.query.order_by(Bundle.is_active.desc(), Bundle.name).all()
    return render_template("bundles/index.html", bundles=bundles,
                           season_choices=SEASON_CHOICES)


# ── Create ────────────────────────────────────────────────────────────────────
@bundles_bp.route("/create", methods=["GET", "POST"])
@login_required
def create():
    products = Product.query.filter_by(is_active=True).order_by(Product.name).all()

    if request.method == "POST":
        bundle = Bundle(
            name        = request.form["name"].strip(),
            description = request.form.get("description", "").strip(),
            price       = float(request.form["price"]),
            is_seasonal = bool(request.form.get("is_seasonal")),
            season_tag  = request.form.get("season_tag") or None,
        )
        db.session.add(bundle)
        db.session.flush()  # get bundle.id before adding items

        _save_items(bundle, request.form)

        db.session.commit()
        flash(f"Bundle '{bundle.name}' created!", "success")
        return redirect(url_for("bundles.index"))

    return render_template("bundles/form.html", bundle=None,
                           products=products, season_choices=SEASON_CHOICES)


# ── Edit ──────────────────────────────────────────────────────────────────────
@bundles_bp.route("/<int:bundle_id>/edit", methods=["GET", "POST"])
@login_required
def edit(bundle_id):
    bundle   = Bundle.query.get_or_404(bundle_id)
    products = Product.query.filter_by(is_active=True).order_by(Product.name).all()

    if request.method == "POST":
        bundle.name        = request.form["name"].strip()
        bundle.description = request.form.get("description", "").strip()
        bundle.price       = float(request.form["price"])
        bundle.is_seasonal = bool(request.form.get("is_seasonal"))
        bundle.season_tag  = request.form.get("season_tag") or None

        # Replace items
        for item in bundle.items:
            db.session.delete(item)
        db.session.flush()
        _save_items(bundle, request.form)

        db.session.commit()
        flash(f"Bundle '{bundle.name}' updated.", "success")
        return redirect(url_for("bundles.index"))

    return render_template("bundles/form.html", bundle=bundle,
                           products=products, season_choices=SEASON_CHOICES)


# ── Toggle active ─────────────────────────────────────────────────────────────
@bundles_bp.route("/<int:bundle_id>/toggle", methods=["POST"])
@login_required
def toggle(bundle_id):
    bundle = Bundle.query.get_or_404(bundle_id)
    bundle.is_active = not bundle.is_active
    db.session.commit()
    flash(f"Bundle '{bundle.name}' {'activated' if bundle.is_active else 'deactivated'}.", "info")
    return redirect(url_for("bundles.index"))


# ── Stock check (AJAX) ────────────────────────────────────────────────────────
@bundles_bp.route("/<int:bundle_id>/stock_check")
@login_required
def stock_check(bundle_id):
    bundle = Bundle.query.get_or_404(bundle_id)
    issues = _check_bundle_stock(bundle)
    can_sell = len(issues) == 0
    return jsonify({"can_sell": can_sell, "issues": issues})


# ── Sell a bundle ─────────────────────────────────────────────────────────────
@bundles_bp.route("/<int:bundle_id>/sell", methods=["POST"])
@login_required
def sell(bundle_id):
    bundle   = Bundle.query.get_or_404(bundle_id)
    quantity = int(request.form.get("quantity", 1))

    issues = _check_bundle_stock(bundle, quantity)
    if issues:
        for msg in issues:
            flash(msg, "danger")
        return redirect(url_for("bundles.index"))

    # Deduct stock for each component
    for item in bundle.items:
        product = item.component
        deduct  = float(item.quantity) * quantity
        product.stock_quantity = (product.stock_quantity or 0) - deduct

        # Record stock movement
        try:
            mv = StockMovement(
                product_id   = product.id,
                movement_type= "sale",
                quantity     = -deduct,
                reference    = f"Bundle Sale: {bundle.name} x{quantity}",
                created_by   = current_user.id,
                created_at   = datetime.datetime.utcnow(),
            )
            db.session.add(mv)
        except Exception:
            pass  # stock movement model shape may differ — adjust field names

    db.session.commit()
    flash(f"Sold {quantity}× '{bundle.name}'. Stock updated.", "success")
    return redirect(url_for("bundles.index"))


# ── Helpers ───────────────────────────────────────────────────────────────────
def _save_items(bundle, form):
    """Parse repeated product_id[] / quantity[] fields from the form."""
    product_ids = form.getlist("product_id[]")
    quantities  = form.getlist("quantity[]")
    for pid, qty in zip(product_ids, quantities):
        if pid and qty:
            db.session.add(BundleItem(
                bundle_id  = bundle.id,
                product_id = int(pid),
                quantity   = float(qty),
            ))


def _check_bundle_stock(bundle, qty=1) -> list:
    """Returns list of error strings; empty = sufficient stock."""
    issues = []
    for item in bundle.items:
        product = item.component
        needed  = float(item.quantity) * qty
        have    = float(product.stock_quantity or 0)
        if have < needed:
            issues.append(
                f"Not enough stock for {product.name}: "
                f"need {needed}g, have {have}g."
            )
    return issues
