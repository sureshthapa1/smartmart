"""API blueprint — JSON endpoints for Chart.js charts and autofill."""

from datetime import date, timedelta

from flask import Blueprint, jsonify, request
from sqlalchemy import func

from ...extensions import db
from ...models.sale import Sale
from ...services import cash_flow_manager
from ...services.decorators import login_required

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
    """Daily profit for the past 30 days."""
    end = date.today()
    start = end - timedelta(days=29)
    labels, data = [], []
    current = start
    while current <= end:
        pl = cash_flow_manager.profit_loss(current, current)
        labels.append(str(current))
        data.append(float(pl["profit"]))
        current += timedelta(days=1)
    return jsonify({"labels": labels, "data": data})


@api_bp.route("/customer-search")
@login_required
def customer_search():
    """Search customers by name or phone for smart billing autofill."""
    q = request.args.get("q", "").strip()
    if len(q) < 1:
        return jsonify([])
    from ...models.customer import Customer
    rows = db.session.execute(
        db.select(Customer)
        .where(
            func.lower(Customer.name).contains(q.lower()) |
            Customer.phone.contains(q)
        )
        .order_by(Customer.visit_count.desc())
        .limit(8)
    ).scalars().all()
    return jsonify([{
        "id": c.id,
        "name": c.name,
        "phone": c.phone or "",
        "address": c.address or "",
        "visits": c.visit_count,
    } for c in rows])


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
    """Customer rank, CLV, churn risk, visit stats for pre-billing panel."""
    from ...models.customer import Customer
    from ...services.ai_customer_intelligence import (
        tier_customers, churn_prediction, customer_lifetime_value
    )
    from datetime import date as dt
    c = db.get_or_404(Customer, customer_id)

    # Tier info
    tiers = tier_customers()
    tier_info = next((t for t in tiers["customers"] if t["id"] == customer_id), None)

    # CLV
    clv = customer_lifetime_value(c.name)

    # Churn risk
    churn = churn_prediction()
    churn_status = None
    for entry in churn.get("churned", []):
        if entry["name"].lower() == c.name.lower():
            churn_status = {"risk": "churned", "days_inactive": entry["days_inactive"],
                            "action": entry["action"]}
            break
    if not churn_status:
        for entry in churn.get("at_risk", []):
            if entry["name"].lower() == c.name.lower():
                churn_status = {"risk": "at_risk", "days_inactive": entry["days_inactive"],
                                "action": entry["action"]}
                break

    last_visit_str = c.last_visit.strftime("%Y-%m-%d") if c.last_visit else None

    return jsonify({
        "id": c.id,
        "name": c.name,
        "tier": tier_info["tier"] if tier_info else "New",
        "tier_color": tier_info["tier_color"] if tier_info else "secondary",
        "tier_icon": tier_info["tier_icon"] if tier_info else "🆕",
        "total_spent": tier_info["total_spent"] if tier_info else 0,
        "frequency": tier_info["frequency"] if tier_info else 0,
        "avg_order": tier_info["avg_order"] if tier_info else 0,
        "last_visit": last_visit_str,
        "recency_days": tier_info["recency_days"] if tier_info else None,
        "score": tier_info["score"] if tier_info else 0,
        "clv": clv.get("estimated_net_clv", 0),
        "clv_tier": clv.get("tier", "—"),
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
