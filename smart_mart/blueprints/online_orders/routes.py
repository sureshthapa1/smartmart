"""Online Orders blueprint — full order management with delivery tracking."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, date, timedelta

from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, url_for, abort
from flask_login import current_user
from sqlalchemy import and_, func

from ...extensions import db
from ...models.online_order import OnlineOrder, OnlineOrderItem
from ...models.product import Product
from ...models.shop_settings import ShopSettings
from ...services.decorators import login_required, admin_required

online_orders_bp = Blueprint("online_orders", __name__, url_prefix="/online-orders")


def _gen_order_number() -> str:
    try:
        s = ShopSettings.get()
        prefix = (s.invoice_prefix or "ORD").replace("INV", "ORD")
    except Exception:
        prefix = "ORD"
    return f"{prefix}-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"


# ── List ──────────────────────────────────────────────────────────────────────

@online_orders_bp.route("/")
@login_required
def list_orders():
    if current_user.role != "admin":
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(current_user.id)
        if not p.can_view_online_orders:
            abort(403)
    status_filter = request.args.get("status", "")
    search = request.args.get("q", "").strip()
    start_raw = request.args.get("start_date", "")
    end_raw = request.args.get("end_date", "")

    stmt = db.select(OnlineOrder).order_by(OnlineOrder.created_at.desc())

    if status_filter:
        stmt = stmt.where(OnlineOrder.status == status_filter)
    if search:
        stmt = stmt.where(
            db.or_(
                OnlineOrder.customer_name.ilike(f"%{search}%"),
                OnlineOrder.customer_phone.ilike(f"%{search}%"),
                OnlineOrder.order_number.ilike(f"%{search}%"),
            )
        )
    if start_raw:
        try:
            stmt = stmt.where(func.date(OnlineOrder.created_at) >= date.fromisoformat(start_raw))
        except ValueError:
            pass
    if end_raw:
        try:
            stmt = stmt.where(func.date(OnlineOrder.created_at) <= date.fromisoformat(end_raw))
        except ValueError:
            pass

    orders = db.session.execute(stmt).scalars().all()

    # Status counts for tabs
    status_counts = {}
    for s in ["pending", "confirmed", "preparing", "out_for_delivery", "delivered", "cancelled"]:
        count = db.session.execute(
            db.select(func.count(OnlineOrder.id)).where(OnlineOrder.status == s)
        ).scalar() or 0
        status_counts[s] = count

    return render_template("online_orders/list.html",
                           orders=orders, status_filter=status_filter,
                           status_counts=status_counts, search=search,
                           start_date=start_raw, end_date=end_raw)


# ── Create ────────────────────────────────────────────────────────────────────

@online_orders_bp.route("/create", methods=["GET", "POST"])
@login_required
def create_order():
    if current_user.role != "admin":
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(current_user.id)
        if not p.can_manage_online_orders:
            abort(403)
    products = db.session.execute(
        db.select(Product).where(Product.quantity > 0).order_by(Product.name)
    ).scalars().all()

    if request.method == "POST":
        # Parse items
        items_data = []
        idx = 0
        while True:
            pid = request.form.get(f"items[{idx}][product_id]")
            if pid is None:
                break
            qty_raw = request.form.get(f"items[{idx}][quantity]", "0")
            price_raw = request.form.get(f"items[{idx}][unit_price]", "0")
            try:
                pid = int(pid)
                qty = int(qty_raw)
                price = float(price_raw)
                if pid > 0 and qty > 0:
                    items_data.append({"product_id": pid, "quantity": qty, "unit_price": price})
            except (ValueError, TypeError):
                pass
            idx += 1

        if not items_data:
            flash("Please add at least one item.", "danger")
            return render_template("online_orders/create.html", products=products)

        subtotal = sum(i["unit_price"] * i["quantity"] for i in items_data)
        delivery_charge = float(request.form.get("delivery_charge", 0) or 0)
        discount = float(request.form.get("discount_amount", 0) or 0)

        # Estimated delivery
        est_raw = request.form.get("estimated_delivery", "")
        est_delivery = None
        if est_raw:
            try:
                est_delivery = datetime.fromisoformat(est_raw)
            except ValueError:
                pass

        order = OnlineOrder(
            order_number=_gen_order_number(),
            customer_name=request.form.get("customer_name", "").strip(),
            customer_phone=request.form.get("customer_phone", "").strip(),
            customer_email=request.form.get("customer_email", "").strip() or None,
            delivery_address=request.form.get("delivery_address", "").strip(),
            delivery_area=request.form.get("delivery_area", "").strip() or None,
            total_amount=subtotal,
            delivery_charge=delivery_charge,
            discount_amount=discount,
            payment_mode=request.form.get("payment_mode", "cod"),
            payment_status="pending",
            status="pending",
            notes=request.form.get("notes", "").strip() or None,
            assigned_to=request.form.get("assigned_to", "").strip() or None,
            order_source=request.form.get("order_source", "website"),
            estimated_delivery=est_delivery,
            created_by=current_user.id,
        )
        db.session.add(order)
        db.session.flush()

        for item in items_data:
            product = db.session.get(Product, item["product_id"])
            db.session.add(OnlineOrderItem(
                order_id=order.id,
                product_id=item["product_id"],
                product_name=product.name if product else f"Product #{item['product_id']}",
                quantity=item["quantity"],
                unit_price=item["unit_price"],
                subtotal=item["unit_price"] * item["quantity"],
            ))
            # Deduct stock when order is created
            if product and product.quantity >= item["quantity"]:
                product.quantity -= item["quantity"]

        db.session.commit()
        flash(f"Online order {order.order_number} created successfully.", "success")
        return redirect(url_for("online_orders.order_detail", order_id=order.id))

    return render_template("online_orders/create.html", products=products)


# ── Detail ────────────────────────────────────────────────────────────────────

@online_orders_bp.route("/<int:order_id>")
@login_required
def order_detail(order_id):
    order = db.get_or_404(OnlineOrder, order_id)
    return render_template("online_orders/detail.html", order=order)


# ── Update Status ─────────────────────────────────────────────────────────────

@online_orders_bp.route("/<int:order_id>/status", methods=["POST"])
@login_required
def update_status(order_id):
    if current_user.role != "admin":
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(current_user.id)
        if not p.can_manage_online_orders:
            abort(403)
    order = db.get_or_404(OnlineOrder, order_id)
    new_status = request.form.get("status", "")
    note = request.form.get("note", "").strip()

    valid = list(OnlineOrder.STATUS_LABELS.keys())
    if new_status not in valid:
        flash("Invalid status.", "danger")
        return redirect(url_for("online_orders.order_detail", order_id=order_id))

    order.status = new_status
    if new_status == "delivered":
        order.delivered_at = datetime.now(timezone.utc)
        order.payment_status = "paid" if order.payment_mode != "cod" else order.payment_status
    elif new_status == "cancelled":
        order.cancelled_at = datetime.now(timezone.utc)
        order.cancel_reason = note or "Cancelled by staff"
        # Restore stock on cancellation
        for item in order.items:
            product = db.session.get(Product, item.product_id)
            if product:
                product.quantity += item.quantity

    if note:
        order.notes = (order.notes or "") + f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {note}"

    db.session.commit()
    flash(f"Order status updated to {new_status}.", "success")
    return redirect(url_for("online_orders.order_detail", order_id=order_id))


# ── Update Payment Status ─────────────────────────────────────────────────────

@online_orders_bp.route("/<int:order_id>/payment", methods=["POST"])
@login_required
def update_payment(order_id):
    if current_user.role != "admin":
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(current_user.id)
        if not p.can_manage_online_orders:
            abort(403)
    order = db.get_or_404(OnlineOrder, order_id)
    order.payment_status = request.form.get("payment_status", order.payment_status)
    db.session.commit()
    flash("Payment status updated.", "success")
    return redirect(url_for("online_orders.order_detail", order_id=order_id))


# ── Delete ────────────────────────────────────────────────────────────────────

@online_orders_bp.route("/<int:order_id>/delete", methods=["POST"])
@admin_required
def delete_order(order_id):
    order = db.get_or_404(OnlineOrder, order_id)
    db.session.delete(order)
    db.session.commit()
    flash("Order deleted.", "success")
    return redirect(url_for("online_orders.list_orders"))


# ── Analytics Dashboard ───────────────────────────────────────────────────────

@online_orders_bp.route("/analytics")
@admin_required
def analytics():
    today = date.today()
    month_start = today.replace(day=1)
    week_start = today - timedelta(days=today.weekday())

    # Summary metrics
    def _revenue(start=None, end=None, status=None):
        stmt = db.select(func.coalesce(func.sum(OnlineOrder.total_amount + OnlineOrder.delivery_charge - OnlineOrder.discount_amount), 0))
        if start:
            stmt = stmt.where(func.date(OnlineOrder.created_at) >= start)
        if end:
            stmt = stmt.where(func.date(OnlineOrder.created_at) <= end)
        if status:
            stmt = stmt.where(OnlineOrder.status == status)
        return float(db.session.execute(stmt).scalar() or 0)

    def _count(start=None, status=None):
        stmt = db.select(func.count(OnlineOrder.id))
        if start:
            stmt = stmt.where(func.date(OnlineOrder.created_at) >= start)
        if status:
            stmt = stmt.where(OnlineOrder.status == status)
        return db.session.execute(stmt).scalar() or 0

    metrics = {
        "today_orders": _count(today),
        "today_revenue": _revenue(today, today),
        "week_orders": _count(week_start),
        "week_revenue": _revenue(week_start, today),
        "month_orders": _count(month_start),
        "month_revenue": _revenue(month_start, today),
        "total_orders": _count(),
        "pending": _count(status="pending"),
        "out_for_delivery": _count(status="out_for_delivery"),
        "delivered": _count(status="delivered"),
        "cancelled": _count(status="cancelled"),
    }

    # Revenue by payment mode
    pm_rows = db.session.execute(
        db.select(OnlineOrder.payment_mode,
                  func.count(OnlineOrder.id).label("count"),
                  func.sum(OnlineOrder.total_amount).label("revenue"))
        .group_by(OnlineOrder.payment_mode)
    ).all()
    payment_breakdown = [{"mode": r.payment_mode or "cod", "count": r.count,
                           "revenue": float(r.revenue or 0)} for r in pm_rows]

    # Revenue by source
    src_rows = db.session.execute(
        db.select(OnlineOrder.order_source,
                  func.count(OnlineOrder.id).label("count"),
                  func.sum(OnlineOrder.total_amount).label("revenue"))
        .group_by(OnlineOrder.order_source)
    ).all()
    source_breakdown = [{"source": r.order_source or "website", "count": r.count,
                          "revenue": float(r.revenue or 0)} for r in src_rows]

    # Daily trend (last 30 days)
    daily = db.session.execute(
        db.select(func.date(OnlineOrder.created_at).label("day"),
                  func.count(OnlineOrder.id).label("count"),
                  func.sum(OnlineOrder.total_amount).label("revenue"))
        .where(func.date(OnlineOrder.created_at) >= today - timedelta(days=29))
        .group_by(func.date(OnlineOrder.created_at))
        .order_by(func.date(OnlineOrder.created_at))
    ).all()
    daily_trend = [{"date": str(r.day), "count": r.count, "revenue": float(r.revenue or 0)} for r in daily]

    # Top delivery areas
    area_rows = db.session.execute(
        db.select(OnlineOrder.delivery_area,
                  func.count(OnlineOrder.id).label("count"),
                  func.sum(OnlineOrder.total_amount).label("revenue"))
        .where(OnlineOrder.delivery_area.isnot(None))
        .group_by(OnlineOrder.delivery_area)
        .order_by(func.count(OnlineOrder.id).desc())
        .limit(10)
    ).all()
    top_areas = [{"area": r.delivery_area, "count": r.count,
                  "revenue": float(r.revenue or 0)} for r in area_rows]

    # Top ordered products
    top_products = db.session.execute(
        db.select(OnlineOrderItem.product_name,
                  func.sum(OnlineOrderItem.quantity).label("qty"),
                  func.sum(OnlineOrderItem.subtotal).label("revenue"))
        .group_by(OnlineOrderItem.product_name)
        .order_by(func.sum(OnlineOrderItem.quantity).desc())
        .limit(10)
    ).all()

    return render_template("online_orders/analytics.html",
                           metrics=metrics,
                           payment_breakdown=payment_breakdown,
                           source_breakdown=source_breakdown,
                           daily_trend=daily_trend,
                           top_areas=top_areas,
                           top_products=top_products)


# ── Tracking API (public-style) ───────────────────────────────────────────────

@online_orders_bp.route("/track/<string:order_number>")
def track_order(order_number):
    """Public order tracking page."""
    order = db.session.execute(
        db.select(OnlineOrder).filter_by(order_number=order_number)
    ).scalar_one_or_none()
    return render_template("online_orders/track.html", order=order,
                           order_number=order_number)


@online_orders_bp.route("/api/track/<string:order_number>")
def api_track(order_number):
    """JSON tracking API."""
    order = db.session.execute(
        db.select(OnlineOrder).filter_by(order_number=order_number)
    ).scalar_one_or_none()
    if not order:
        return jsonify({"error": "Order not found"}), 404

    label, color = order.status_label
    return jsonify({
        "order_number": order.order_number,
        "status": order.status,
        "status_label": label,
        "customer_name": order.customer_name,
        "total": order.grand_total,
        "payment_mode": order.payment_mode,
        "payment_status": order.payment_status,
        "estimated_delivery": str(order.estimated_delivery) if order.estimated_delivery else None,
        "delivered_at": str(order.delivered_at) if order.delivered_at else None,
        "items": [{"name": i.product_name, "qty": i.quantity,
                   "price": float(i.unit_price)} for i in order.items],
    })
