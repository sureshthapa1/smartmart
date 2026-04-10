"""Promotions blueprint — manage time-based discounts and deals (Feature #6)."""
from __future__ import annotations

from datetime import date

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from ...extensions import db
from ...models.category import Category
from ...services import promotion_service
from ...services.decorators import admin_required, login_required

promotions_bp = Blueprint("promotions", __name__, url_prefix="/promotions")


@promotions_bp.route("/")
@admin_required
def list_promotions():
    promos = promotion_service.list_promotions()
    today = date.today()
    return render_template("promotions/list.html", promos=promos, today=today)


@promotions_bp.route("/create", methods=["GET", "POST"])
@admin_required
def create_promotion():
    categories = db.session.execute(db.select(Category).order_by(Category.name)).scalars().all()
    if request.method == "POST":
        data = _form_to_data(request.form)
        data["created_by"] = current_user.id
        try:
            promo = promotion_service.create_promotion(data)
            flash(f"Promotion '{promo.name}' created.", "success")
            return redirect(url_for("promotions.list_promotions"))
        except Exception as e:
            flash(str(e), "danger")
    return render_template("promotions/form.html", promo=None, categories=categories,
                           today=date.today().isoformat(), action="Create")


@promotions_bp.route("/<int:promo_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_promotion(promo_id):
    from ...models.promotion import Promotion
    promo = db.get_or_404(Promotion, promo_id)
    categories = db.session.execute(db.select(Category).order_by(Category.name)).scalars().all()
    if request.method == "POST":
        data = _form_to_data(request.form)
        try:
            promotion_service.update_promotion(promo_id, data)
            flash("Promotion updated.", "success")
            return redirect(url_for("promotions.list_promotions"))
        except Exception as e:
            flash(str(e), "danger")
    return render_template("promotions/form.html", promo=promo, categories=categories,
                           today=date.today().isoformat(), action="Edit")


@promotions_bp.route("/<int:promo_id>/delete", methods=["POST"])
@admin_required
def delete_promotion(promo_id):
    try:
        promotion_service.delete_promotion(promo_id)
        flash("Promotion deleted.", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("promotions.list_promotions"))


@promotions_bp.route("/api/check", methods=["POST"])
@login_required
def check_promotions():
    """API: check applicable promotions for a cart subtotal."""
    data = request.get_json() or {}
    subtotal = float(data.get("subtotal", 0))
    items = data.get("items", [])
    promos = promotion_service.get_active_promotions_for_cart(items, subtotal)
    return jsonify({"promotions": promos})


def _form_to_data(form) -> dict:
    data = {
        "name": form.get("name", "").strip(),
        "promo_type": form.get("promo_type", "percentage"),
        "discount_value": float(form.get("discount_value", 0) or 0),
        "scope": form.get("scope", "all"),
        "scope_value": form.get("scope_value", "").strip() or None,
        "is_active": form.get("is_active") == "on",
    }
    min_p = form.get("min_purchase", "").strip()
    data["min_purchase"] = float(min_p) if min_p else None
    buy_qty = form.get("buy_qty", "").strip()
    data["buy_qty"] = int(buy_qty) if buy_qty else None
    free_qty = form.get("free_qty", "").strip()
    data["free_qty"] = int(free_qty) if free_qty else None
    try:
        data["start_date"] = date.fromisoformat(form.get("start_date", ""))
    except ValueError:
        data["start_date"] = date.today()
    try:
        data["end_date"] = date.fromisoformat(form.get("end_date", ""))
    except ValueError:
        data["end_date"] = date.today()
    return data
