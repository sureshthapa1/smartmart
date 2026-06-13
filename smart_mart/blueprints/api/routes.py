"""API blueprint — JSON endpoints for Chart.js charts and autofill."""

from datetime import date, timedelta

from flask import Blueprint, jsonify, request
from flask_login import current_user
from sqlalchemy import func

from ...extensions import db, limiter
from ...models.sale import Sale
from ...services.decorators import login_required, admin_required

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.route("/sales-trend")
@login_required
def sales_trend():
    """Daily sales totals for the past 30 days."""
    end = date.today()
    start = end - timedelta(days=29)
    rows = db.session.execute(
        db.select(func.date(Sale.sale_date).label("day"), func.sum(Sale.total_amount).label("total"))
        .where(func.date(Sale.sale_date) >= start)
        .group_by(func.date(Sale.sale_date))
        .order_by(func.date(Sale.sale_date))
    ).all()
    labels = [str(r.day) for r in rows]
    data = [float(r.total) for r in rows]
    return jsonify({"labels": labels, "data": data})


@api_bp.route("/profit-trend")
@login_required
def profit_trend():
    """Daily profit for the past 30 days — single aggregated query."""
    from ...models.sale import SaleItem
    from ...models.product import Product
    from ...models.expense import Expense
    from sqlalchemy import and_, cast, Numeric

    end = date.today()
    start = end - timedelta(days=29)

    # Revenue per day
    rev_rows = db.session.execute(
        db.select(
            func.date(Sale.sale_date).label("day"),
            func.sum(Sale.total_amount).label("revenue"),
        )
        .where(func.date(Sale.sale_date) >= start)
        .group_by(func.date(Sale.sale_date))
    ).all()
    revenue_by_day = {str(r.day): float(r.revenue) for r in rev_rows}

    # COGS per day
    cogs_rows = db.session.execute(
        db.select(
            func.date(Sale.sale_date).label("day"),
            func.sum(Product.cost_price * SaleItem.quantity).label("cogs"),
        )
        .join(SaleItem, SaleItem.sale_id == Sale.id)
        .join(Product, Product.id == SaleItem.product_id)
        .where(func.date(Sale.sale_date) >= start)
        .group_by(func.date(Sale.sale_date))
    ).all()
    cogs_by_day = {str(r.day): float(r.cogs) for r in cogs_rows}

    # Expenses per day
    exp_rows = db.session.execute(
        db.select(
            Expense.expense_date.label("day"),
            func.sum(Expense.amount).label("expenses"),
        )
        .where(and_(Expense.expense_date >= start, Expense.expense_date <= end))
        .group_by(Expense.expense_date)
    ).all()
    exp_by_day = {str(r.day): float(r.expenses) for r in exp_rows}

    labels, data = [], []
    current = start
    while current <= end:
        day_str = str(current)
        rev = revenue_by_day.get(day_str, 0)
        cogs = cogs_by_day.get(day_str, 0)
        exp = exp_by_day.get(day_str, 0)
        labels.append(day_str)
        data.append(round(rev - cogs - exp, 2))
        current += timedelta(days=1)

    return jsonify({"labels": labels, "data": data})


@api_bp.route("/customer-search")
@login_required
def customer_search():
    """Search saved customers and historical sale names for billing autofill."""
    q = request.args.get("q", "").strip()
    if len(q) < 1:
        return jsonify([])
    from ...models.customer import Customer
    term = f"%{q.lower()}%"

    customer_rows = db.session.execute(
        db.select(Customer)
        .where(
            db.or_(
                func.lower(Customer.name).like(term),
                func.lower(func.coalesce(Customer.phone, "")).like(term),
                func.lower(func.coalesce(Customer.address, "")).like(term),
            )
        )
        .order_by(Customer.visit_count.desc(), Customer.last_visit.desc(), Customer.name)
        .limit(12)
    ).scalars().all()

    suggestions = {}

    def add_suggestion(name, phone="", address="", visits=0, customer_id=None, last_visit=None):
        clean_name = (name or "").strip()
        if not clean_name or clean_name.lower() == "walk-in customer":
            return
        clean_phone = (phone or "").strip()
        clean_address = (address or "").strip()
        key = (clean_name.lower(), clean_phone)
        existing = suggestions.get(key)
        if existing is None or visits > existing["visits"]:
            suggestions[key] = {
                "id": customer_id,
                "name": clean_name,
                "phone": clean_phone,
                "address": clean_address,
                "visits": int(visits or 0),
                "_last_visit": last_visit,
            }
        elif existing:
            if customer_id and not existing.get("id"):
                existing["id"] = customer_id
            if clean_address and not existing.get("address"):
                existing["address"] = clean_address
            existing["visits"] = max(existing["visits"], int(visits or 0))
            if last_visit and (not existing.get("_last_visit") or last_visit > existing["_last_visit"]):
                existing["_last_visit"] = last_visit

    for c in customer_rows:
        add_suggestion(
            c.name,
            c.phone,
            c.address,
            c.visit_count,
            c.id,
            c.last_visit,
        )

    sale_rows = db.session.execute(
        db.select(
            Sale.customer_name.label("name"),
            Sale.customer_phone.label("phone"),
            Sale.customer_address.label("address"),
            func.count(Sale.id).label("visits"),
            func.max(Sale.sale_date).label("last_visit"),
        )
        .where(
            Sale.customer_name.isnot(None),
            func.length(func.trim(Sale.customer_name)) > 0,
            func.lower(Sale.customer_name) != "walk-in customer",
            db.or_(
                func.lower(Sale.customer_name).like(term),
                func.lower(func.coalesce(Sale.customer_phone, "")).like(term),
                func.lower(func.coalesce(Sale.customer_address, "")).like(term),
            ),
        )
        .group_by(Sale.customer_name, Sale.customer_phone, Sale.customer_address)
        .order_by(func.count(Sale.id).desc(), func.max(Sale.sale_date).desc())
        .limit(20)
    ).all()

    for row in sale_rows:
        matched_customer = None
        if row.phone:
            matched_customer = db.session.execute(
                db.select(Customer).where(
                    Customer.phone == row.phone,
                    func.lower(Customer.name) == row.name.strip().lower(),
                )
            ).scalar_one_or_none()
        if matched_customer is None:
            matched_customer = db.session.execute(
                db.select(Customer)
                .where(func.lower(Customer.name) == row.name.strip().lower())
                .order_by(Customer.visit_count.desc())
            ).scalars().first()

        add_suggestion(
            row.name,
            row.phone,
            row.address,
            row.visits,
            matched_customer.id if matched_customer else None,
            row.last_visit,
        )

    results = sorted(
        suggestions.values(),
        key=lambda item: (
            item["visits"],
            item["_last_visit"].isoformat() if item.get("_last_visit") else "",
            item["name"].lower(),
        ),
        reverse=True,
    )[:8]

    for item in results:
        item.pop("_last_visit", None)

    return jsonify(results)


@api_bp.route("/customers/<int:customer_id>")
@login_required
def customer_detail(customer_id):
    """Basic customer details for autofill."""
    from ...models.customer import Customer
    c = db.get_or_404(Customer, customer_id)
    return jsonify({
        "id": c.id,
        "name": c.name,
        "phone": c.phone or "",
        "address": c.address or "",
        "visits": c.visit_count,
    })


@api_bp.route("/customers/<int:customer_id>/intelligence")
@login_required
def customer_intelligence(customer_id):
    """Customer rank, CLV, churn risk, visit stats for pre-billing panel.
    Uses direct queries instead of loading all customers for speed.
    """
    from ...models.customer import Customer
    from ...models.sale import Sale, SaleItem
    from ...models.product import Product
    from datetime import date as dt
    from sqlalchemy import and_

    c = db.get_or_404(Customer, customer_id)
    today = dt.today()

    # Get this customer's sales directly
    sales = db.session.execute(
        db.select(Sale)
        .where(func.lower(Sale.customer_name) == c.name.lower())
        .order_by(Sale.sale_date.desc())
    ).scalars().all()

    frequency = len(sales)
    total_spent = sum(float(s.total_amount) for s in sales)
    avg_order = round(total_spent / frequency, 2) if frequency else 0
    last_sale_date = sales[0].sale_date.date() if sales else None
    recency_days = (today - last_sale_date).days if last_sale_date else None

    # Compute tier score
    monetary_score = min(50, total_spent / 1000 * 10)
    frequency_score = min(30, frequency * 3)
    recency_score = max(0, 20 - (recency_days or 999) * 0.2)
    total_score = monetary_score + frequency_score + recency_score

    if total_score >= 70:
        tier, tier_color, tier_icon = "Platinum", "primary", "💎"
    elif total_score >= 50:
        tier, tier_color, tier_icon = "Gold", "warning", "🥇"
    elif total_score >= 30:
        tier, tier_color, tier_icon = "Silver", "secondary", "🥈"
    elif frequency > 0:
        tier, tier_color, tier_icon = "Bronze", "danger", "🥉"
    else:
        tier, tier_color, tier_icon = "New", "secondary", "🆕"

    # CLV (simple calculation inline)
    dates = sorted([s.sale_date.date() for s in sales if s.sale_date])
    if len(dates) >= 2:
        lifespan_days = (dates[-1] - dates[0]).days
        purchase_rate = frequency / max(lifespan_days / 30, 1)
    else:
        purchase_rate = frequency
    predicted_3yr = avg_order * purchase_rate * 12 * 3
    clv = round(predicted_3yr * 0.25, 2)

    # Churn check
    churn_status = None
    if recency_days is not None:
        three_months_ago = today - __import__('datetime').timedelta(days=90)
        six_months_ago = today - __import__('datetime').timedelta(days=180)
        recent_count = sum(1 for s in sales if s.sale_date and s.sale_date.date() >= three_months_ago)
        prev_count = sum(1 for s in sales if s.sale_date and
                         six_months_ago <= s.sale_date.date() < three_months_ago)
        declining = prev_count > 0 and recent_count < prev_count * 0.5
        if recency_days > 90:
            churn_status = {"risk": "churned", "days_inactive": recency_days,
                            "action": "Win-back campaign with 20% discount"}
        elif recency_days > 45 or declining:
            churn_status = {"risk": "at_risk", "days_inactive": recency_days,
                            "action": "Send retention offer: 10% discount"}

    return jsonify({
        "id": c.id,
        "name": c.name,
        "tier": tier,
        "tier_color": tier_color,
        "tier_icon": tier_icon,
        "total_spent": round(total_spent, 2),
        "frequency": frequency,
        "avg_order": avg_order,
        "last_visit": str(last_sale_date) if last_sale_date else None,
        "recency_days": recency_days,
        "score": round(total_score, 1),
        "clv": clv,
        "clv_tier": "High Value" if clv > 5000 else "Medium Value" if clv > 1000 else "Low Value",
        "churn": churn_status,
    })


@api_bp.route("/customers/<int:customer_id>/recommendations")
@login_required
def customer_recommendations(customer_id):
    """AI offers and suggestions for pre-billing panel."""
    from ...models.customer import Customer
    from ...services.ai_customer_intelligence import loyalty_offers, personalized_recommendations
    c = db.get_or_404(Customer, customer_id)
    offers = loyalty_offers(c.name)
    recs = personalized_recommendations(c.name)
    return jsonify({
        "offers": offers.get("offers", []),
        "best_offer": offers.get("best_offer"),
        "cross_sell": recs.get("cross_sell", [])[:3],
        "combo_suggestions": recs.get("combo_suggestions", [])[:2],
    })


@api_bp.route("/customers/<int:customer_id>/offer-feedback", methods=["POST"])
@login_required
def offer_feedback(customer_id):
    """Track whether AI offer was applied or ignored (self-learning)."""
    data = request.get_json() or {}
    applied = data.get("applied", False)
    offer_text = data.get("offer", "")
    try:
        from ...models.ai_memory import AIRecommendation
        from datetime import datetime, timezone
        rec = AIRecommendation(
            category="billing_offer",
            title=offer_text[:200],
            reason=f"Customer ID {customer_id} — billing offer",
            entity_type="customer",
            entity_id=customer_id,
            status="accepted" if applied else "rejected",
            acted_at=datetime.now(timezone.utc),
        )
        db.session.add(rec)
        db.session.commit()
    except Exception:
        pass
    return jsonify({"ok": True})


@api_bp.route("/alerts/top5")
@login_required
def alerts_top5():
    """Top 5 most critical alerts for the notification bell dropdown."""
    from ...services.alert_engine import get_low_stock_alerts, get_expiry_alerts
    from datetime import date
    alerts = []

    # Out-of-stock first
    for p in get_low_stock_alerts():
        if p.quantity == 0:
            alerts.append({"icon": "🔴", "title": f"{p.name} — OUT OF STOCK",
                           "detail": "Restock immediately", "priority": 0})
        else:
            alerts.append({"icon": "🟡", "title": f"{p.name} — Low Stock",
                           "detail": f"Only {p.quantity} units left", "priority": 1})

    # Expiry alerts
    today = date.today()
    for p in get_expiry_alerts():
        days = (p.expiry_date - today).days
        alerts.append({"icon": "⏰", "title": f"{p.name} — Expiring Soon",
                       "detail": f"Expires in {days} day{'s' if days != 1 else ''}",
                       "priority": 2})

    alerts.sort(key=lambda x: x["priority"])
    return jsonify(alerts[:5])


@api_bp.route("/nlg/dismiss", methods=["POST"])
@login_required
def nlg_dismiss():
    """Dismiss the NLG daily summary banner."""
    from flask import session as flask_session
    flask_session.pop("nlg_summary_text", None)
    flask_session.modified = True
    return jsonify({"ok": True})


@api_bp.route("/product-icon", methods=["GET", "POST"])
@login_required
def product_icon():
    """GET: get stored emoji for a product name. POST: save custom emoji."""
    if request.method == "POST":
        data = request.get_json() or {}
        name = data.get("name", "").strip()
        emoji = data.get("emoji", "").strip()
        if name and emoji:
            from ...models.product_icon_map import ProductIconMap
            ProductIconMap.set(name, emoji)
            return jsonify({"ok": True})
        return jsonify({"ok": False}), 400

    name = request.args.get("name", "").strip()
    if not name:
        return jsonify({"emoji": None})
    from ...models.product_icon_map import ProductIconMap
    emoji = ProductIconMap.get(name)
    return jsonify({"emoji": emoji})


# ---------------------------------------------------------------------------
# Loyalty Wallet
# ---------------------------------------------------------------------------

@api_bp.route("/loyalty/wallet", methods=["GET"])
@login_required
def loyalty_wallet():
    from ...services import loyalty_wallet_service
    customer_name = request.args.get("customer_name", "").strip()
    customer_phone = request.args.get("customer_phone", "").strip() or None
    # Use get_or_create_wallet only for real named customers — never commit
    # on a GET request (prevents ghost wallet records for walk-in customers)
    wallet = loyalty_wallet_service.get_or_create_wallet(customer_name, customer_phone)
    if wallet and wallet.id is None:
        # New wallet not yet persisted — don't commit on a read-only request
        db.session.rollback()
        return jsonify({"available": False})
    return jsonify(loyalty_wallet_service.wallet_snapshot(wallet))


@api_bp.route("/loyalty/redeem-preview", methods=["POST"])
@login_required
def loyalty_redeem_preview():
    from ...services import loyalty_wallet_service
    data = request.get_json() or {}
    customer_name = (data.get("customer_name") or "").strip()
    customer_phone = (data.get("customer_phone") or "").strip() or None
    requested_points = int(data.get("points", 0) or data.get("requested_points", 0) or 0)
    gross_total = float(data.get("gross_total", 0) or 0)
    wallet = loyalty_wallet_service.get_or_create_wallet(customer_name, customer_phone)
    preview = loyalty_wallet_service.preview_redeem(wallet, requested_points, gross_total)
    # Do NOT commit — this is a preview-only endpoint. Wallet creation (if any)
    # is intentionally not persisted here; it happens during actual sale creation.
    db.session.rollback()
    _, rpp = loyalty_wallet_service._get_loyalty_rates()
    return jsonify({
        "redeemed_points": preview["redeemed_points"],
        "discount": preview["discount"],
        "payable_total": preview["payable_total"],
        "rupee_per_point": float(rpp),
        "wallet": loyalty_wallet_service.wallet_snapshot(wallet),
        "preview": preview,
    })


# ---------------------------------------------------------------------------
# Duplicate / Fake Customer Detection
# ---------------------------------------------------------------------------

@api_bp.route("/customers/duplicates/detect", methods=["POST"])
@admin_required
def detect_customer_duplicates():
    from ...services import customer_quality_service
    flags = customer_quality_service.detect_duplicates(trigger_user_id=current_user.id)
    return jsonify({"created_flags": len(flags)})


@api_bp.route("/customers/duplicates", methods=["GET"])
@admin_required
def list_customer_duplicates():
    from ...services import customer_quality_service
    status = request.args.get("status", "pending")
    flags = customer_quality_service.list_duplicate_flags(status=status)
    return jsonify([
        {
            "id": f.id,
            "primary_customer_id": f.primary_customer_id,
            "primary_customer_name": f.primary_customer.name if f.primary_customer else None,
            "duplicate_customer_id": f.duplicate_customer_id,
            "duplicate_customer_name": f.duplicate_customer.name if f.duplicate_customer else None,
            "confidence": float(f.confidence),
            "reason": f.reason,
            "suspicious": bool(f.suspicious),
            "status": f.status,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in flags
    ])


@api_bp.route("/customers/duplicates/<int:flag_id>/approve", methods=["POST"])
@admin_required
def approve_customer_duplicate(flag_id):
    from ...services import customer_quality_service
    flag = customer_quality_service.approve_merge(flag_id, admin_user_id=current_user.id)
    return jsonify({"id": flag.id, "status": flag.status})


@api_bp.route("/customers/duplicates/<int:flag_id>/reject", methods=["POST"])
@admin_required
def reject_customer_duplicate(flag_id):
    from ...services import customer_quality_service
    flag = customer_quality_service.reject_merge(flag_id, admin_user_id=current_user.id)
    return jsonify({"id": flag.id, "status": flag.status})


# ---------------------------------------------------------------------------
# Offline -> Online Sync
# ---------------------------------------------------------------------------

@api_bp.route("/sync/push", methods=["POST"])
@login_required
def sync_push():
    from ...services import sync_service
    data = request.get_json() or {}
    device_id = (data.get("device_id") or "").strip()
    events = data.get("events") or []
    if not device_id:
        return jsonify({"error": "device_id is required"}), 400
    if not isinstance(events, list):
        return jsonify({"error": "events must be a list"}), 400
    result = sync_service.push_events(device_id=device_id, events=events)
    return jsonify(result)


@api_bp.route("/sync/pull", methods=["GET"])
@login_required
def sync_pull():
    from ...services import sync_service
    device_id = request.args.get("device_id", "").strip()
    since_event_id = int(request.args.get("since_event_id", 0) or 0)
    if not device_id:
        return jsonify({"error": "device_id is required"}), 400
    result = sync_service.pull_events(device_id=device_id, since_event_id=since_event_id)
    return jsonify(result)


@api_bp.route("/sync/conflicts/<int:sync_event_id>/resolve", methods=["POST"])
@admin_required
def resolve_sync_conflict(sync_event_id):
    from ...services import sync_service
    data = request.get_json() or {}
    strategy = data.get("strategy", "server_wins")
    try:
        result = sync_service.resolve_conflict(sync_event_id, strategy)
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ---------------------------------------------------------------------------
# Competitor Price Tracking
# ---------------------------------------------------------------------------

@api_bp.route("/pricing/competitor", methods=["POST"])
@admin_required
def add_competitor_price():
    from ...services import competitor_pricing_service
    data = request.get_json() or {}
    try:
        entry = competitor_pricing_service.add_competitor_price(
            product_id=int(data.get("product_id")),
            competitor_name=(data.get("competitor_name") or "").strip(),
            competitor_price=float(data.get("competitor_price")),
            captured_by_user_id=current_user.id,
            notes=data.get("notes"),
        )
        return jsonify({"entry_id": entry.id}), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@api_bp.route("/pricing/competitor/<int:product_id>", methods=["GET"])
@login_required
def compare_competitor_price(product_id):
    from ...services import competitor_pricing_service
    result = competitor_pricing_service.compare_product_price(product_id)
    return jsonify(result)


@api_bp.route("/pricing/suggestions/<int:product_id>", methods=["POST"])
@admin_required
def pricing_suggestion(product_id):
    from ...services import competitor_pricing_service
    try:
        result = competitor_pricing_service.generate_pricing_suggestion(product_id)
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@api_bp.route("/customer-risk/<path:customer_name>")
@login_required
def customer_risk(customer_name):
    """Return risk score, tier, and outstanding balance for a customer (Req 5.5)."""
    from ...services.credit_risk_service import get_risk_for_customer
    from ...models.sale import Sale
    from sqlalchemy import func as _func

    name = customer_name.strip()
    if not name:
        return jsonify({"risk_score": 0, "risk_tier": "safe", "risk_label": "🟢 Safe",
                        "total_outstanding": 0.0}), 200

    data = get_risk_for_customer(name)

    # Compute outstanding on-the-fly for accuracy
    from ...models.operations import CustomerCreditPayment
    credit_sales = db.session.execute(
        db.select(Sale)
        .where(
            _func.lower(Sale.customer_name) == name.lower(),
            Sale.payment_mode == "credit",
        )
    ).scalars().all()

    total_outstanding = 0.0
    for s in credit_sales:
        paid = db.session.execute(
            db.select(_func.coalesce(_func.sum(CustomerCreditPayment.amount), 0))
            .where(CustomerCreditPayment.sale_id == s.id)
        ).scalar() or 0
        total_outstanding += max(0.0, float(s.total_amount) - float(paid))

    return jsonify({
        "customer_name": name,
        "risk_score": data["score"],
        "risk_tier": data["risk_level"],
        "risk_label": data["risk_label"],
        "risk_color": data["risk_color"],
        "total_outstanding": round(total_outstanding, 2),
        "has_override": data.get("has_override", False),
    })


@api_bp.route("/products/<int:product_id>/price-history")
@login_required
def product_price_history(product_id):
    """Return last 5 purchase prices for a product from each supplier."""
    from ...models.purchase import Purchase, PurchaseItem
    from ...models.supplier import Supplier
    rows = db.session.execute(
        db.select(
            PurchaseItem.unit_cost,
            Purchase.purchase_date,
            Supplier.name.label("supplier_name"),
        )
        .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
        .join(Supplier, Supplier.id == Purchase.supplier_id)
        .where(PurchaseItem.product_id == product_id)
        .order_by(Purchase.purchase_date.desc())
        .limit(5)
    ).all()
    return jsonify([
        {"cost": float(r.unit_cost), "date": str(r.purchase_date), "supplier": r.supplier_name}
        for r in rows
    ])


# ── Bot Runner endpoint ───────────────────────────────────────────────────────

@api_bp.route("/bot/run", methods=["POST"])
def run_bots():
    """Run all daily bots. Protected by BOT_SECRET header or admin session.

    Call from a cron job / Render scheduler:
        curl -X POST https://your-app.onrender.com/api/bot/run \
             -H "X-Bot-Secret: YOUR_BOT_SECRET"

    Or trigger manually from the admin panel.
    """
    import os
    from flask import current_app

    # Auth: either valid admin session OR correct BOT_SECRET header
    bot_secret = os.environ.get("BOT_SECRET", "")
    header_secret = request.headers.get("X-Bot-Secret", "")

    is_admin_session = (
        current_user.is_authenticated and current_user.role == "admin"
    )
    is_valid_secret = bot_secret and header_secret == bot_secret

    if not is_admin_session and not is_valid_secret:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        from ...services.bot_runner import run_all_bots
        user_id = current_user.id if current_user.is_authenticated else 1
        result = run_all_bots(user_id=user_id)
        current_app.logger.info("Bot runner completed: %s", result)
        return jsonify(result)
    except Exception as e:
        current_app.logger.exception("Bot runner failed: %s", e)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/bot/status", methods=["GET"])
@login_required
def bot_status():
    """Return today's bot run results from notification log."""
    from ...models.operations import AppNotification
    from datetime import datetime, timezone

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    notifications = db.session.execute(
        db.select(AppNotification)
        .where(AppNotification.created_at >= today_start)
        .order_by(AppNotification.created_at.desc())
        .limit(50)
    ).scalars().all()

    by_type = {}
    for n in notifications:
        t = n.notification_type
        by_type[t] = by_type.get(t, 0) + 1

    return jsonify({
        "today_notifications": len(notifications),
        "by_type": by_type,
        "latest": [
            {
                "type": n.notification_type,
                "message": n.message[:100],
                "created_at": n.created_at.isoformat(),
            }
            for n in notifications[:10]
        ],
    })


# ── Global Search ─────────────────────────────────────────────────────────────

@api_bp.route("/search")
@login_required
def global_search():
    """Global search — uses AI smart search (Claude NLP) when key is set,
    falls back to fast keyword search. Used by the topbar search bar.
    """
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"results": []})

    # For longer, natural language queries use AI smart search
    if len(q) > 10 and " " in q:
        try:
            from ...services.ai_smart_search import smart_search
            result = smart_search(q)
            if result.get("results"):
                return jsonify({"results": result["results"][:12]})
        except Exception:
            pass  # fall through to keyword search below


    term = f"%{q.lower()}%"
    results = []

    # Products
    from ...models.product import Product
    products = db.session.execute(
        db.select(Product)
        .where(
            db.or_(
                db.func.lower(Product.name).like(term),
                db.func.lower(Product.sku).like(term),
            )
        )
        .where(Product.is_active == True)
        .limit(5)
    ).scalars().all()
    for p in products:
        results.append({
            "type": "product",
            "icon": "bi-box-seam",
            "label": p.name,
            "sub": f"SKU: {p.sku} | Stock: {p.quantity}",
            "url": f"/inventory/{p.id}/edit",
        })

    # Sales / invoices
    from ...models.sale import Sale
    sales = db.session.execute(
        db.select(Sale)
        .where(
            db.or_(
                db.func.lower(Sale.invoice_number).like(term),
                db.func.lower(Sale.customer_name).like(term),
            )
        )
        .order_by(Sale.sale_date.desc())
        .limit(5)
    ).scalars().all()
    for s in sales:
        results.append({
            "type": "sale",
            "icon": "bi-receipt",
            "label": s.invoice_number or f"Sale #{s.id}",
            "sub": f"{s.customer_name or 'Walk-in'} | NPR {float(s.total_amount):,.0f}",
            "url": f"/sales/{s.id}",
        })

    # Customers
    from ...models.customer import Customer
    customers = db.session.execute(
        db.select(Customer)
        .where(
            db.or_(
                db.func.lower(Customer.name).like(term),
                db.func.lower(Customer.phone).like(term),
            )
        )
        .limit(5)
    ).scalars().all()
    for c in customers:
        results.append({
            "type": "customer",
            "icon": "bi-person",
            "label": c.name,
            "sub": f"Phone: {c.phone or '-'} | Tier: {c.loyalty_tier or 'bronze'}",
            "url": f"/customers/{c.id}",
        })

    # Suppliers
    from ...models.supplier import Supplier
    suppliers = db.session.execute(
        db.select(Supplier)
        .where(db.func.lower(Supplier.name).like(term))
        .limit(3)
    ).scalars().all()
    for s in suppliers:
        results.append({
            "type": "supplier",
            "icon": "bi-truck",
            "label": s.name,
            "sub": f"Contact: {s.contact or '-'}",
            "url": f"/purchases/suppliers/{s.id}/edit",
        })

    return jsonify({"results": results[:12]})


# ── POS Cart Validator ────────────────────────────────────────────────────────

@api_bp.route("/validate-cart", methods=["POST"])
@limiter.limit("60 per minute")
@login_required
def validate_cart():
    """Pre-submission cart validation using AI invoice detector.
    Called by the POS before confirming a sale to catch errors early.
    Returns warnings and errors the cashier can act on before committing.
    """
    from ...services.ai_invoice_detector import validate_sale_items
    data = request.get_json() or {}
    items = data.get("items", [])
    discount = float(data.get("discount_amount", 0))
    if not items:
        return jsonify({"valid": True, "warnings": [], "errors": []})
    result = validate_sale_items(items, discount)
    return jsonify({
        "valid": len(result.get("errors", [])) == 0,
        "warnings": result.get("warnings", []),
        "errors": result.get("errors", []),
        "summary": result.get("summary", ""),
    })


# ── Expense Auto-Categorizer ──────────────────────────────────────────────────

@api_bp.route("/suggest-expense-category", methods=["POST"])
@limiter.limit("30 per minute")
@login_required
def suggest_expense_category():
    """Return AI-suggested category + icon for an expense note.
    Called live from the expense form as the user types the description.
    """
    from ...services.ai_expense_categorizer import categorize_expense
    data = request.get_json() or {}
    note = data.get("note", "").strip()
    amount = float(data.get("amount", 0))
    if not note:
        return jsonify({"category": "", "label": "", "icon": "", "confidence": 0})
    result = categorize_expense(note, amount)
    return jsonify(result)


# ── Alert count AJAX endpoint ────────────────────────────────────────────────
# Called from base.html every 60 s (replaces the expensive context_processor
# DB query that ran on every single page render for every logged-in user).

@api_bp.route("/alert-count")
@limiter.limit("60 per minute")
@login_required
def alert_count():
    """Return sidebar badge counts: alert count + pending online orders."""
    from ...services.cache_service import get as _cache_get, set as _cache_set
    cache_key = f"alert_count:u{current_user.id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)
    try:
        from ...services.alert_engine import get_low_stock_alerts, get_expiry_alerts
        from ...models.dismissed_alert import DismissedAlert
        from ...models.online_order import OnlineOrder
        dismissed = set(
            db.session.execute(
                db.select(DismissedAlert.alert_key)
                .where(DismissedAlert.user_id == current_user.id)
            ).scalars().all()
        )
        low_stock = [p for p in get_low_stock_alerts() if f"low_stock:{p.id}" not in dismissed]
        expiry    = [p for p in get_expiry_alerts()   if f"expiry:{p.id}"    not in dismissed]
        count     = len(low_stock) + len(expiry)
        pending_orders = 0
        if current_user.role == "admin":
            pending_orders = db.session.execute(
                db.select(db.func.count(OnlineOrder.id))
                .where(OnlineOrder.status == "pending")
            ).scalar() or 0
        result = {"global_alert_count": count, "pending_orders_count": pending_orders}
        _cache_set(cache_key, result, ttl=60)
        return jsonify(result)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("alert_count endpoint error: %s", exc)
        return jsonify({"global_alert_count": 0, "pending_orders_count": 0})
