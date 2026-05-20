from datetime import datetime, timezone

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from ...extensions import db
from ...models.bundle import Bundle, BundleItem
from ...models.product import Product
from ...models.sale import Sale
from ...models.stock_movement import StockMovement
from ...services.decorators import admin_required, login_required

bundles_bp = Blueprint("bundles", __name__, url_prefix="/bundles")
SEASONS = ["Dashain", "Tihar", "Wedding", "General"]


@bundles_bp.route("/")
@login_required
def index():
    season = request.args.get("season", "All")
    stmt = db.select(Bundle).order_by(Bundle.name)
    if season in SEASONS:
        stmt = stmt.where(Bundle.season_tag == season)
    bundles = db.session.execute(stmt).scalars().all()
    return render_template("bundles/index.html", bundles=bundles, seasons=SEASONS, active_season=season)


@bundles_bp.route("/create", methods=["GET", "POST"])
@admin_required
def create_bundle():
    return _bundle_form()


@bundles_bp.route("/<int:bundle_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_bundle(bundle_id):
    bundle = db.get_or_404(Bundle, bundle_id)
    return _bundle_form(bundle)


@bundles_bp.route("/<int:bundle_id>/toggle", methods=["POST"])
@admin_required
def toggle(bundle_id):
    bundle = db.get_or_404(Bundle, bundle_id)
    try:
        bundle.is_active = not bundle.is_active
        db.session.commit()
        flash(f"{bundle.name} is now {'active' if bundle.is_active else 'inactive'}.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Could not update bundle: {exc}", "danger")
    return redirect(url_for("bundles.index"))


@bundles_bp.route("/<int:bundle_id>/stock_check")
@login_required
def stock_check(bundle_id):
    bundle = db.get_or_404(Bundle, bundle_id)
    units = max(1, request.args.get("units", 1, type=int))
    issues = _stock_issues(bundle, units)
    return jsonify({"can_sell": not issues, "issues": issues})


@bundles_bp.route("/<int:bundle_id>/sell", methods=["POST"])
@login_required
def sell(bundle_id):
    bundle = db.get_or_404(Bundle, bundle_id)
    units = max(1, int(request.form.get("units", 1) or 1))
    payment_method = request.form.get("payment_method", "cash")
    issues = _stock_issues(bundle, units)
    if issues:
        for issue in issues:
            flash(issue, "danger")
        return redirect(url_for("bundles.index"))

    try:
        sale = Sale(
            user_id=current_user.id,
            total_amount=float(bundle.price) * units,
            sale_date=datetime.now(timezone.utc),
            customer_name=request.form.get("customer_name", "").strip() or None,
            payment_mode=payment_method,
            payment_method=payment_method,
            sale_type="bundle",
        )
        db.session.add(sale)
        db.session.flush()
        summaries = []
        for item in bundle.items:
            deduct_qty = int(float(item.quantity) * units)
            item.component.quantity -= deduct_qty
            summaries.append(f"{item.component.name}: -{deduct_qty}g")
            db.session.add(StockMovement(
                product_id=item.component.id,
                change_amount=-deduct_qty,
                change_type="sale",
                reference_id=sale.id,
                note=f"Bundle Sale: {bundle.name} x{units}",
                created_by=current_user.id,
                timestamp=datetime.now(timezone.utc),
            ))
        db.session.commit()
        flash(f"Sold {bundle.name} x{units}. " + "; ".join(summaries), "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Could not sell bundle: {exc}", "danger")
    return redirect(url_for("bundles.index"))


def _bundle_form(bundle=None):
    products = db.session.execute(
        db.select(Product).where(Product.is_active == True).order_by(Product.name)
    ).scalars().all()

    if request.method == "POST":
        try:
            target = bundle or Bundle()
            target.name = request.form.get("name", "").strip()
            target.description = request.form.get("description", "").strip() or None
            target.price = float(request.form.get("price", 0) or 0)
            target.is_seasonal = request.form.get("is_seasonal") == "on"
            target.season_tag = request.form.get("season_tag", "General")
            target.is_active = request.form.get("is_active", "on") == "on"
            if not target.name or target.price <= 0:
                raise ValueError("Bundle name and price are required.")
            target.items.clear()
            product_ids = request.form.getlist("product_id")
            quantities = request.form.getlist("quantity")
            for product_id, quantity in zip(product_ids, quantities):
                if not product_id or not quantity:
                    continue
                qty = float(quantity)
                if qty <= 0:
                    continue
                target.items.append(BundleItem(product_id=int(product_id), quantity=qty))
            if not target.items:
                raise ValueError("Add at least one component.")
            db.session.add(target)
            db.session.commit()
            flash("Bundle saved successfully.", "success")
            return redirect(url_for("bundles.index"))
        except Exception as exc:
            db.session.rollback()
            flash(f"Could not save bundle: {exc}", "danger")

    return render_template("bundles/form.html", bundle=bundle, products=products, seasons=SEASONS)


def _stock_issues(bundle, units):
    issues = []
    for item in bundle.items:
        need = float(item.quantity) * units
        have = float(item.component.quantity or 0)
        if have < need:
            issues.append(f"Not enough {item.component.name}: need {need:g}g, have {have:g}g")
    return issues
