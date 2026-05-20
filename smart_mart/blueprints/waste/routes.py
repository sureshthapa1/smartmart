from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from ...extensions import db
from ...models.product import Product
from ...models.stock_movement import StockMovement
from ...models.waste_record import WasteRecord
from ...services.decorators import login_required

waste_bp = Blueprint("waste", __name__, url_prefix="/waste")

REASONS = ["expired", "damaged", "spilled", "rodent", "theft", "quality_reject", "other"]


@waste_bp.route("/")
@login_required
def index():
    start_raw = request.args.get("start_date", "")
    end_raw = request.args.get("end_date", "")
    reason = request.args.get("reason", "")

    stmt = db.select(WasteRecord).order_by(WasteRecord.created_at.desc())
    try:
        if start_raw:
            stmt = stmt.where(func.date(WasteRecord.created_at) >= date.fromisoformat(start_raw))
        if end_raw:
            stmt = stmt.where(func.date(WasteRecord.created_at) <= date.fromisoformat(end_raw))
    except ValueError:
        flash("Invalid date filter.", "warning")
    if reason:
        stmt = stmt.where(WasteRecord.reason == reason)

    records = db.session.execute(stmt.limit(200)).scalars().all()
    products = db.session.execute(
        db.select(Product).where(Product.is_active == True).order_by(Product.name)
    ).scalars().all()
    return render_template(
        "waste/index.html",
        records=records,
        products=products,
        reasons=REASONS,
        start_date=start_raw,
        end_date=end_raw,
        reason=reason,
    )


@waste_bp.route("/record", methods=["POST"])
@login_required
def record_waste():
    try:
        product_id = int(request.form.get("product_id", 0) or 0)
        quantity = float(request.form.get("quantity", 0) or 0)
        reason = request.form.get("reason", "").strip()
        notes = request.form.get("notes", "").strip() or None
        if quantity <= 0:
            raise ValueError("Quantity must be greater than zero.")
        if reason not in REASONS:
            raise ValueError("Choose a valid waste reason.")

        product = db.get_or_404(Product, product_id)
        if quantity > float(product.quantity or 0):
            raise ValueError(f"Insufficient stock for {product.name}.")
        cost_value = quantity * float(product.cost_price or 0)
        product.quantity = int(float(product.quantity or 0) - quantity)
        record = WasteRecord(
            product_id=product.id,
            quantity=quantity,
            reason=reason,
            cost_value=cost_value,
            notes=notes,
            recorded_by=current_user.id,
        )
        db.session.add(record)
        db.session.add(StockMovement(
            product_id=product.id,
            change_amount=-int(quantity),
            change_type="waste",
            note=f"Waste: {reason}",
            created_by=current_user.id,
        ))
        db.session.commit()
        flash(f"Waste recorded for {product.name}: {quantity:g}g.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Could not record waste: {exc}", "danger")
    return redirect(url_for("waste.index"))


@waste_bp.route("/report")
@login_required
def report():
    today = date.today()
    month_start = today.replace(day=1)
    total = db.session.execute(
        db.select(func.coalesce(func.sum(WasteRecord.cost_value), 0))
        .where(func.date(WasteRecord.created_at) >= month_start)
    ).scalar() or 0
    by_reason = db.session.execute(
        db.select(WasteRecord.reason, func.coalesce(func.sum(WasteRecord.cost_value), 0).label("total"))
        .where(func.date(WasteRecord.created_at) >= month_start)
        .group_by(WasteRecord.reason)
        .order_by(func.sum(WasteRecord.cost_value).desc())
    ).all()
    by_product = db.session.execute(
        db.select(Product.name, func.coalesce(func.sum(WasteRecord.quantity), 0).label("qty"),
                  func.coalesce(func.sum(WasteRecord.cost_value), 0).label("total"))
        .join(Product, Product.id == WasteRecord.product_id)
        .where(func.date(WasteRecord.created_at) >= month_start)
        .group_by(Product.name)
        .order_by(func.sum(WasteRecord.cost_value).desc())
        .limit(5)
    ).all()

    previous_month_end = month_start
    previous_month_start = (month_start.replace(day=1) - __import__("datetime").timedelta(days=1)).replace(day=1)
    previous_total = db.session.execute(
        db.select(func.coalesce(func.sum(WasteRecord.cost_value), 0))
        .where(func.date(WasteRecord.created_at) >= previous_month_start)
        .where(func.date(WasteRecord.created_at) < previous_month_end)
    ).scalar() or 0
    return render_template(
        "waste/report.html",
        total=float(total),
        by_reason=by_reason,
        by_product=by_product,
        previous_total=float(previous_total),
    )
