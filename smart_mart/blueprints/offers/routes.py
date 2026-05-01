"""Offers blueprint — Customer Retention & Offer System.

Routes:
  GET  /offers/                    — offer management dashboard
  GET  /offers/create              — create new offer form
  POST /offers/create              — save new offer
  GET  /offers/<id>/edit           — edit offer form
  POST /offers/<id>/edit           — save offer edits
  POST /offers/<id>/toggle         — activate / deactivate offer
  GET  /offers/analytics           — offer performance analytics
  GET  /offers/customer/<id>       — offers for a specific customer
  POST /offers/assign              — assign offer to customer (AJAX)
  POST /offers/apply               — apply offer to current sale (AJAX)
  POST /offers/rollback            — rollback offer on sale cancel (AJAX)
  GET  /offers/api/customer-offers — fetch active offers for customer (AJAX, billing UI)
  POST /offers/api/send-notification — send offer notification manually
  POST /offers/api/run-cron        — trigger cron jobs (expiry + reminders)
  GET  /offers/api/ai-suggest      — AI offer suggestions for a customer
"""
from __future__ import annotations

from datetime import date, timedelta

from flask import (
    Blueprint, Response, abort, flash, jsonify,
    redirect, render_template, request, url_for,
)
from flask_login import current_user

from ...extensions import db
from ...models.offer import Offer, CustomerOffer, OfferNotification
from ...models.customer import Customer
from ...services.decorators import login_required
from ...services import offer_service

offers_bp = Blueprint("offers", __name__, url_prefix="/offers")


def _require_perm(perm: str):
    if current_user.role != "admin":
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(current_user.id)
        if not getattr(p, perm, False):
            abort(403)


# ── Offer Management ──────────────────────────────────────────────────────────

@offers_bp.route("/")
@login_required
def list_offers():
    _require_perm("can_view_offers")
    page = request.args.get("page", 1, type=int)
    status_filter = request.args.get("status", "")
    per_page = 25

    stmt = db.select(Offer).order_by(Offer.created_at.desc())
    if status_filter in ("active", "inactive"):
        stmt = stmt.where(Offer.status == status_filter)

    total = db.session.execute(
        db.select(db.func.count()).select_from(stmt.subquery())
    ).scalar() or 0
    offers = db.session.execute(
        stmt.limit(per_page).offset((page - 1) * per_page)
    ).scalars().all()

    return render_template(
        "offers/list.html",
        offers=offers,
        page=page,
        per_page=per_page,
        total=total,
        status_filter=status_filter,
    )


@offers_bp.route("/create", methods=["GET", "POST"])
@login_required
def create_offer():
    _require_perm("can_manage_offers")
    from ...models.product import Product
    products = db.session.execute(
        db.select(Product).order_by(Product.name)
    ).scalars().all()

    if request.method == "POST":
        try:
            offer = _save_offer_from_form(request.form, creator_id=current_user.id)
            db.session.add(offer)
            db.session.commit()
            flash(f"Offer '{offer.title}' created successfully.", "success")
            return redirect(url_for("offers.list_offers"))
        except ValueError as e:
            flash(str(e), "danger")

    return render_template("offers/create.html", products=products, offer=None)


@offers_bp.route("/<int:offer_id>/edit", methods=["GET", "POST"])
@login_required
def edit_offer(offer_id):
    _require_perm("can_manage_offers")
    offer = db.get_or_404(Offer, offer_id)
    from ...models.product import Product
    products = db.session.execute(db.select(Product).order_by(Product.name)).scalars().all()

    if request.method == "POST":
        try:
            _update_offer_from_form(offer, request.form)
            db.session.commit()
            flash(f"Offer '{offer.title}' updated.", "success")
            return redirect(url_for("offers.list_offers"))
        except ValueError as e:
            flash(str(e), "danger")

    return render_template("offers/create.html", products=products, offer=offer)


@offers_bp.route("/<int:offer_id>/toggle", methods=["POST"])
@login_required
def toggle_offer(offer_id):
    _require_perm("can_manage_offers")
    offer = db.get_or_404(Offer, offer_id)
    offer.status = "inactive" if offer.status == "active" else "active"
    db.session.commit()
    flash(f"Offer '{offer.title}' is now {offer.status}.", "success")
    return redirect(url_for("offers.list_offers"))


@offers_bp.route("/<int:offer_id>/clone", methods=["GET"])
@login_required
def clone_offer(offer_id):
    """Clone an existing offer — pre-fills the create form."""
    _require_perm("can_manage_offers")
    offer = db.get_or_404(Offer, offer_id)
    from ...models.product import Product
    products = db.session.execute(db.select(Product).order_by(Product.name)).scalars().all()
    # Pass a "clone" object with same fields but no id
    clone = Offer(
        title=f"Copy of {offer.title}",
        description=offer.description,
        offer_type=offer.offer_type,
        discount_value=offer.discount_value,
        min_purchase_amount=offer.min_purchase_amount,
        product_id=offer.product_id,
        usage_limit=offer.usage_limit,
        valid_days=offer.valid_days,
        created_by=current_user.id,
        status="active",
    )
    return render_template("offers/create.html", products=products, offer=clone, is_clone=True)


@offers_bp.route("/analytics")
@login_required
def analytics():
    _require_perm("can_view_offers")
    stats = offer_service.get_offer_analytics()
    return render_template("offers/analytics.html", stats=stats)


@offers_bp.route("/retention")
@login_required
def retention_dashboard():
    """Customer retention dashboard — inactive, birthday, VIP customers."""
    _require_perm("can_view_offers")
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import func as _func
    from ...models.sale import Sale
    from ...models.customer import Customer as _Cust

    today = date.today()
    now_utc = datetime.now(timezone.utc)
    cutoff_inactive = now_utc - timedelta(days=14)

    # ── Inactive customers (no visit in 14+ days, have phone) ────────────
    inactive_raw = db.session.execute(
        db.select(_Cust)
        .where(
            _Cust.last_visit < cutoff_inactive,
            _Cust.phone.isnot(None),
            _Cust.phone != "",
        )
        .order_by(_Cust.last_visit.asc())
        .limit(50)
    ).scalars().all()

    # Enrich with total_spent and days_inactive
    inactive_customers = []
    for c in inactive_raw:
        lv = c.last_visit
        lv_date = lv.date() if hasattr(lv, "date") else lv
        days_inactive = (today - lv_date).days if lv_date else 999
        total_spent = db.session.execute(
            db.select(_func.sum(Sale.total_amount))
            .where(
                (Sale.customer_id == c.id) |
                (_func.lower(Sale.customer_name) == c.name.lower())
            )
        ).scalar() or 0
        c.days_inactive = days_inactive
        c.total_spent = float(total_spent)
        inactive_customers.append(c)

    # ── Birthday customers (next 7 days) ─────────────────────────────────
    all_customers = db.session.execute(
        db.select(_Cust).where(_Cust.birthday.isnot(None))
    ).scalars().all()
    birthday_customers = []
    for c in all_customers:
        bday = c.birthday.replace(year=today.year)
        if bday < today:
            bday = c.birthday.replace(year=today.year + 1)
        days_to = (bday - today).days
        if 0 <= days_to <= 7:
            c.days_to_birthday = days_to
            birthday_customers.append(c)
    birthday_customers.sort(key=lambda c: c.days_to_birthday)

    # ── VIP / High-value customers (top 20 by visit_count) ───────────────
    vip_raw = db.session.execute(
        db.select(_Cust)
        .order_by(_Cust.visit_count.desc())
        .limit(20)
    ).scalars().all()
    vip_customers = []
    for c in vip_raw:
        total_spent = db.session.execute(
            db.select(_func.sum(Sale.total_amount))
            .where(
                (Sale.customer_id == c.id) |
                (_func.lower(Sale.customer_name) == c.name.lower())
            )
        ).scalar() or 0
        c.total_spent = float(total_spent)
        vip_customers.append(c)
    vip_customers.sort(key=lambda c: c.total_spent, reverse=True)

    # ── Active offers for the dropdowns ──────────────────────────────────
    active_offers = db.session.execute(
        db.select(Offer).where(Offer.status == "active").order_by(Offer.title)
    ).scalars().all()

    return render_template(
        "offers/retention_dashboard.html",
        inactive_customers=inactive_customers,
        birthday_customers=birthday_customers,
        vip_customers=vip_customers,
        active_offers=active_offers,
        today=today,
    )


@offers_bp.route("/customer/<int:customer_id>")
@login_required
def customer_offers(customer_id):
    _require_perm("can_view_offers")
    customer = db.get_or_404(Customer, customer_id)
    cos = db.session.execute(
        db.select(CustomerOffer)
        .where(CustomerOffer.customer_id == customer_id)
        .order_by(CustomerOffer.created_at.desc())
    ).scalars().all()
    offers = db.session.execute(
        db.select(Offer).where(Offer.status == "active").order_by(Offer.title)
    ).scalars().all()
    ai_suggestions = []
    try:
        ai_suggestions = offer_service.ai_suggest_offers_for_customer(customer_id)
    except Exception:
        pass
    return render_template(
        "offers/customer_offers.html",
        customer=customer, cos=cos, offers=offers,
        ai_suggestions=ai_suggestions,
    )


# ── AJAX Endpoints ────────────────────────────────────────────────────────────

@offers_bp.route("/api/customer-offers")
@login_required
def api_customer_offers():
    """Return active offers for a customer — called from billing UI on customer select."""
    customer_id = request.args.get("customer_id", type=int)
    if not customer_id:
        return jsonify({"offers": [], "best": None})

    offers = offer_service.get_active_offers_for_customer(customer_id)
    cart_total = request.args.get("cart_total", 0.0, type=float)
    best = offer_service.get_best_offer_for_customer(customer_id, cart_total) if cart_total else None
    return jsonify({"offers": offers, "best": best})


@offers_bp.route("/api/assign", methods=["POST"])
@login_required
def api_assign_offer():
    """Assign an offer to a customer (called from billing UI or customer profile)."""
    _require_perm("can_assign_offers")
    data = request.get_json(silent=True) or request.form
    try:
        customer_id = int(data.get("customer_id", 0))
        offer_id = int(data.get("offer_id", 0))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid customer_id or offer_id"}), 400

    sale_id = data.get("sale_id")
    try:
        sale_id = int(sale_id) if sale_id else None
    except (TypeError, ValueError):
        sale_id = None

    if not customer_id or not offer_id:
        return jsonify({"success": False, "error": "customer_id and offer_id required"}), 400

    # Validate customer and offer exist
    from ...models.customer import Customer as _Cust
    if not db.session.get(_Cust, customer_id):
        return jsonify({"success": False, "error": "Customer not found"}), 404
    offer = db.session.get(Offer, offer_id)
    if not offer:
        return jsonify({"success": False, "error": "Offer not found"}), 404
    if offer.status != "active":
        return jsonify({"success": False, "error": "Offer is not active"}), 400

    try:
        co = offer_service.assign_offer_to_customer(
            customer_id=customer_id,
            offer_id=offer_id,
            assigned_at_sale_id=sale_id,
            send_notification=True,
        )
        is_dup = getattr(co, "_is_duplicate", False)
        return jsonify({
            "success": True,
            "customer_offer_id": co.id,
            "is_duplicate": is_dup,
            "message": (
                f"Offer already assigned (expires {co.expiry_date.strftime('%d %b %Y')})"
                if is_dup else
                f"Offer assigned successfully (expires {co.expiry_date.strftime('%d %b %Y')})"
            ),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@offers_bp.route("/api/apply", methods=["POST"])
@login_required
def api_apply_offer():
    """Apply an offer to a sale — validates and returns discount amount."""
    _require_perm("can_apply_offers")
    data = request.get_json(silent=True) or request.form
    try:
        customer_offer_id = int(data.get("customer_offer_id", 0))
        sale_id = int(data.get("sale_id", 0))
        cart_total = float(data.get("cart_total", 0))
        product_subtotal = float(data.get("product_subtotal", 0))
        customer_id = int(data.get("customer_id", 0)) or None
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid parameters"}), 400

    if not customer_offer_id or cart_total <= 0:
        return jsonify({"success": False, "error": "customer_offer_id and cart_total required"}), 400

    try:
        result = offer_service.apply_offer(
            customer_offer_id=customer_offer_id,
            sale_id=sale_id,
            cart_total=cart_total,
            product_subtotal=product_subtotal,
            customer_id=customer_id,
        )
        return jsonify({"success": True, **result})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400


@offers_bp.route("/api/rollback", methods=["POST"])
@login_required
def api_rollback_offer():
    """Rollback an offer when a sale is cancelled."""
    data = request.get_json(silent=True) or request.form
    sale_id = int(data.get("sale_id", 0))
    if not sale_id:
        return jsonify({"success": False, "error": "sale_id required"}), 400
    reverted = offer_service.rollback_offer(sale_id)
    return jsonify({"success": True, "reverted": reverted})


@offers_bp.route("/api/send-notification", methods=["POST"])
@login_required
def api_send_notification():
    """Manually send an offer notification to a customer."""
    _require_perm("can_manage_offers")
    data = request.get_json(silent=True) or request.form
    customer_offer_id = int(data.get("customer_offer_id", 0))
    if not customer_offer_id:
        return jsonify({"success": False, "error": "customer_offer_id required"}), 400

    co = db.session.get(CustomerOffer, customer_offer_id)
    if not co:
        return jsonify({"success": False, "error": "Customer offer not found"}), 404

    try:
        offer_service._send_offer_assigned_notification(co)
        return jsonify({"success": True, "message": "Notification sent"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@offers_bp.route("/api/run-cron", methods=["POST"])
@login_required
def api_run_cron():
    """Manually trigger cron jobs (expiry + reminders). Admin only."""
    if current_user.role != "admin":
        abort(403)
    expired = offer_service.expire_stale_offers()
    reminder_counts = offer_service.send_expiry_reminders()
    retried = offer_service.retry_failed_notifications()
    return jsonify({
        "success": True,
        "expired": expired,
        "reminders": reminder_counts,
        "retried": retried,
    })


@offers_bp.route("/api/ai-suggest")
@login_required
def api_ai_suggest():
    """Return AI-generated offer suggestions for a customer."""
    customer_id = request.args.get("customer_id", type=int)
    if not customer_id:
        return jsonify({"suggestions": []})
    suggestions = offer_service.ai_suggest_offers_for_customer(customer_id)
    return jsonify({"suggestions": suggestions})


@offers_bp.route("/api/all-active")
@login_required
def api_all_active_offers():
    """Return all active offers for the 'next visit' dropdown in billing UI."""
    offers = db.session.execute(
        db.select(Offer)
        .where(Offer.status == "active")
        .order_by(Offer.title)
    ).scalars().all()
    return jsonify({
        "offers": [
            {
                "id": o.id,
                "title": o.title,
                "offer_type": o.offer_type,
                "type_label": o.type_label,
                "discount_value": float(o.discount_value),
            }
            for o in offers
        ]
    })


@offers_bp.route("/api/quick-create", methods=["POST"])
@login_required
def api_quick_create():
    """Quick-create an offer from billing UI and immediately assign it."""
    _require_perm("can_manage_offers")
    data = request.get_json(silent=True) or request.form
    try:
        customer_id = int(data.get("customer_id", 0))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid customer_id"}), 400

    sale_id = data.get("sale_id")
    try:
        sale_id = int(sale_id) if sale_id else None
    except (TypeError, ValueError):
        sale_id = None

    if not customer_id:
        return jsonify({"success": False, "error": "customer_id required"}), 400

    try:
        title = str(data.get("title", "Special Offer")).strip()[:120]
        if not title:
            return jsonify({"success": False, "error": "Offer title is required"}), 400

        offer_type = data.get("offer_type", "percentage")
        if offer_type not in Offer.OFFER_TYPES:
            return jsonify({"success": False, "error": "Invalid offer type"}), 400

        discount_value = float(data.get("discount_value", 10))
        if discount_value <= 0:
            return jsonify({"success": False, "error": "Discount value must be > 0"}), 400
        if offer_type in ("percentage", "conditional") and discount_value > 100:
            return jsonify({"success": False, "error": "Percentage cannot exceed 100%"}), 400
        if offer_type in ("fixed", "combo") and discount_value > 100_000:
            return jsonify({"success": False, "error": "Fixed discount cannot exceed NPR 1,00,000"}), 400

        min_purchase_raw = float(data.get("min_purchase_amount", 0) or 0)
        min_purchase = min_purchase_raw if min_purchase_raw > 0 else None
        valid_days = max(1, min(365, int(data.get("valid_days", 30) or 30)))
        usage_limit = max(1, min(100, int(data.get("usage_limit", 1) or 1)))

        offer = Offer(
            title=title,
            description=str(data.get("description", "")).strip()[:500] or None,
            offer_type=offer_type,
            discount_value=discount_value,
            min_purchase_amount=min_purchase,
            valid_days=valid_days,
            usage_limit=usage_limit,
            created_by=current_user.id,
            status="active",
        )
        db.session.add(offer)
        db.session.flush()

        co = offer_service.assign_offer_to_customer(
            customer_id=customer_id,
            offer_id=offer.id,
            assigned_at_sale_id=sale_id,
            send_notification=True,
        )
        return jsonify({
            "success": True,
            "offer_id": offer.id,
            "customer_offer_id": co.id,
            "message": f"Offer '{offer.title}' created and assigned (expires {co.expiry_date.strftime('%d %b %Y')})",
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 400


# ── Form helpers ──────────────────────────────────────────────────────────────

def _save_offer_from_form(form, creator_id: int) -> Offer:
    title = form.get("title", "").strip()
    if not title:
        raise ValueError("Offer title is required.")
    if len(title) > 120:
        raise ValueError("Offer title must be 120 characters or less.")

    offer_type = form.get("offer_type", "percentage")
    if offer_type not in Offer.OFFER_TYPES:
        raise ValueError(f"Invalid offer type: {offer_type}")

    try:
        discount_value = float(form.get("discount_value", 0) or 0)
    except (TypeError, ValueError):
        raise ValueError("Discount value must be a number.")
    if discount_value <= 0:
        raise ValueError("Discount value must be greater than 0.")
    # Cap percentage at 100%, fixed/combo at 100,000 NPR
    if offer_type in ("percentage", "conditional") and discount_value > 100:
        raise ValueError("Percentage discount cannot exceed 100%.")
    if offer_type in ("fixed", "combo") and discount_value > 100_000:
        raise ValueError("Fixed discount cannot exceed NPR 1,00,000.")

    try:
        valid_days = int(form.get("valid_days", 30) or 30)
    except (TypeError, ValueError):
        raise ValueError("Valid days must be a whole number.")
    if valid_days < 1 or valid_days > 365:
        raise ValueError("Valid days must be between 1 and 365.")

    try:
        usage_limit = int(form.get("usage_limit", 1) or 1)
    except (TypeError, ValueError):
        raise ValueError("Usage limit must be a whole number.")
    if usage_limit < 1 or usage_limit > 100:
        raise ValueError("Usage limit must be between 1 and 100.")

    product_id = form.get("product_id") or None
    if product_id:
        try:
            product_id = int(product_id)
        except (TypeError, ValueError):
            raise ValueError("Invalid product selected.")

    try:
        min_purchase = float(form.get("min_purchase_amount", 0) or 0) or None
    except (TypeError, ValueError):
        min_purchase = None

    # Optional scheduling
    from datetime import date as _date
    start_date = None
    end_date = None
    try:
        sd = form.get("start_date", "").strip()
        if sd:
            start_date = _date.fromisoformat(sd)
    except (ValueError, AttributeError):
        pass
    try:
        ed = form.get("end_date", "").strip()
        if ed:
            end_date = _date.fromisoformat(ed)
    except (ValueError, AttributeError):
        pass

    return Offer(
        title=title,
        description=(form.get("description", "").strip() or None),
        offer_type=offer_type,
        discount_value=discount_value,
        min_purchase_amount=min_purchase,
        product_id=product_id,
        usage_limit=usage_limit,
        valid_days=valid_days,
        start_date=start_date,
        end_date=end_date,
        created_by=creator_id,
        status="active",
    )


def _update_offer_from_form(offer: Offer, form) -> None:
    title = form.get("title", "").strip()
    if not title:
        raise ValueError("Offer title is required.")
    if len(title) > 120:
        raise ValueError("Offer title must be 120 characters or less.")

    offer_type = form.get("offer_type", "percentage")
    if offer_type not in Offer.OFFER_TYPES:
        raise ValueError(f"Invalid offer type: {offer_type}")

    try:
        discount_value = float(form.get("discount_value", 0) or 0)
    except (TypeError, ValueError):
        raise ValueError("Discount value must be a number.")
    if discount_value <= 0:
        raise ValueError("Discount value must be greater than 0.")
    if offer_type in ("percentage", "conditional") and discount_value > 100:
        raise ValueError("Percentage discount cannot exceed 100%.")
    if offer_type in ("fixed", "combo") and discount_value > 100_000:
        raise ValueError("Fixed discount cannot exceed NPR 1,00,000.")

    try:
        valid_days = int(form.get("valid_days", 30) or 30)
    except (TypeError, ValueError):
        raise ValueError("Valid days must be a whole number.")
    if valid_days < 1 or valid_days > 365:
        raise ValueError("Valid days must be between 1 and 365.")

    try:
        usage_limit = int(form.get("usage_limit", 1) or 1)
    except (TypeError, ValueError):
        raise ValueError("Usage limit must be a whole number.")
    if usage_limit < 1 or usage_limit > 100:
        raise ValueError("Usage limit must be between 1 and 100.")

    product_id = form.get("product_id") or None
    if product_id:
        try:
            product_id = int(product_id)
        except (TypeError, ValueError):
            raise ValueError("Invalid product selected.")

    try:
        min_purchase = float(form.get("min_purchase_amount", 0) or 0) or None
    except (TypeError, ValueError):
        min_purchase = None

    # Optional scheduling
    from datetime import date as _date
    start_date = None
    end_date = None
    try:
        sd = form.get("start_date", "").strip()
        if sd:
            start_date = _date.fromisoformat(sd)
    except (ValueError, AttributeError):
        pass
    try:
        ed = form.get("end_date", "").strip()
        if ed:
            end_date = _date.fromisoformat(ed)
    except (ValueError, AttributeError):
        pass

    offer.title = title
    offer.description = form.get("description", "").strip() or None
    offer.offer_type = offer_type
    offer.discount_value = discount_value
    offer.min_purchase_amount = min_purchase
    offer.product_id = product_id
    offer.usage_limit = usage_limit
    offer.valid_days = valid_days
    offer.start_date = start_date
    offer.end_date = end_date
