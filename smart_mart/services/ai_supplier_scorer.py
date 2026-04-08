"""AI Module 4: Supplier Performance Scoring System

Ranks suppliers based on:
- Price consistency (variance in unit costs)
- Volume supplied
- Product diversity
- Purchase frequency
"""

from __future__ import annotations

from datetime import date, timedelta
from statistics import mean, stdev

from sqlalchemy import func

from ..extensions import db
from ..models.supplier import Supplier
from ..models.purchase import Purchase, PurchaseItem
from ..models.product import Product


def score_supplier(supplier_id: int) -> dict:
    """Generate a performance scorecard for a single supplier."""
    supplier = db.session.get(Supplier, supplier_id)
    if not supplier:
        return {}

    purchases = db.session.execute(
        db.select(Purchase).filter_by(supplier_id=supplier_id)
        .order_by(Purchase.purchase_date.desc())
    ).scalars().all()

    if not purchases:
        return {
            "supplier_id": supplier_id,
            "supplier_name": supplier.name,
            "score": 0,
            "grade": "N/A",
            "message": "No purchase history.",
        }

    total_purchases = len(purchases)
    total_value = sum(float(p.total_cost) for p in purchases)

    # ── Price Consistency Score (0–30) ────────────────────────────────────
    # For each product from this supplier, check unit cost variance
    product_costs = {}
    for purchase in purchases:
        items = db.session.execute(
            db.select(PurchaseItem).filter_by(purchase_id=purchase.id)
        ).scalars().all()
        for item in items:
            if item.product_id not in product_costs:
                product_costs[item.product_id] = []
            product_costs[item.product_id].append(float(item.unit_cost))

    consistency_scores = []
    for pid, costs in product_costs.items():
        if len(costs) >= 2:
            avg = mean(costs)
            sd = stdev(costs)
            cv = (sd / avg) * 100 if avg > 0 else 0  # coefficient of variation
            # Lower CV = more consistent = higher score
            score = max(0, 30 - cv)
            consistency_scores.append(score)
    price_consistency_score = round(mean(consistency_scores), 1) if consistency_scores else 20.0

    # ── Volume Score (0–25) ───────────────────────────────────────────────
    # More total value = higher score (capped)
    volume_score = min(25.0, round(total_value / 10000 * 25, 1))

    # ── Frequency Score (0–25) ────────────────────────────────────────────
    # Purchases per month over last 6 months
    six_months_ago = date.today() - timedelta(days=180)
    recent_count = sum(1 for p in purchases if p.purchase_date >= six_months_ago)
    frequency_score = min(25.0, round(recent_count / 6 * 25, 1))

    # ── Product Diversity Score (0–20) ────────────────────────────────────
    unique_products = len(product_costs)
    diversity_score = min(20.0, round(unique_products * 2, 1))

    total_score = round(price_consistency_score + volume_score + frequency_score + diversity_score, 1)

    # Grade
    if total_score >= 80:
        grade, grade_color = "A", "success"
    elif total_score >= 65:
        grade, grade_color = "B", "info"
    elif total_score >= 50:
        grade, grade_color = "C", "warning"
    else:
        grade, grade_color = "D", "danger"

    # Last purchase
    last_purchase = purchases[0] if purchases else None

    return {
        "supplier_id": supplier_id,
        "supplier_name": supplier.name,
        "contact": supplier.contact,
        "total_score": total_score,
        "grade": grade,
        "grade_color": grade_color,
        "breakdown": {
            "price_consistency": {"score": price_consistency_score, "max": 30,
                                   "label": "Price Consistency"},
            "volume": {"score": volume_score, "max": 25, "label": "Purchase Volume"},
            "frequency": {"score": frequency_score, "max": 25, "label": "Order Frequency"},
            "diversity": {"score": diversity_score, "max": 20, "label": "Product Diversity"},
        },
        "stats": {
            "total_purchases": total_purchases,
            "total_value": round(total_value, 2),
            "unique_products": unique_products,
            "last_purchase_date": str(last_purchase.purchase_date) if last_purchase else None,
            "recent_6m_orders": recent_count,
        },
        "recommendation": _supplier_recommendation(grade, price_consistency_score, frequency_score),
    }


def _supplier_recommendation(grade: str, consistency: float, frequency: float) -> str:
    if grade == "A":
        return "Excellent supplier. Prioritize for bulk orders and long-term contracts."
    elif grade == "B":
        return "Good supplier. Consider negotiating better pricing for consistency."
    elif grade == "C":
        if consistency < 15:
            return "Price inconsistency detected. Request fixed pricing agreements."
        return "Average performance. Monitor closely and explore alternatives."
    else:
        return "Poor performance. Consider switching to alternative suppliers."


def supplier_scorecard_all() -> dict:
    """Generate scorecards for all suppliers."""
    suppliers = db.session.execute(db.select(Supplier).order_by(Supplier.name)).scalars().all()
    scorecards = []
    for s in suppliers:
        card = score_supplier(s.id)
        if card:
            scorecards.append(card)

    scorecards.sort(key=lambda x: x.get("total_score", 0), reverse=True)

    return {
        "total_suppliers": len(scorecards),
        "scorecards": scorecards,
        "top_supplier": scorecards[0]["supplier_name"] if scorecards else None,
        "generated_at": str(date.today()),
    }
