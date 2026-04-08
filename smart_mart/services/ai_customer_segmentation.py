"""AI Feature 2: Customer Segmentation

Classifies customers as: VIP, Regular, One-time, At-risk
Uses RFM (Recency, Frequency, Monetary) scoring.
"""

from __future__ import annotations
from datetime import date, timedelta
from sqlalchemy import func
from ..extensions import db
from ..models.sale import Sale
from ..models.customer import Customer


def _rfm_score(recency_days: int, frequency: int, monetary: float) -> dict:
    """Score each RFM dimension 1-5."""
    # Recency: lower days = higher score
    if recency_days <= 7:
        r = 5
    elif recency_days <= 30:
        r = 4
    elif recency_days <= 60:
        r = 3
    elif recency_days <= 90:
        r = 2
    else:
        r = 1

    # Frequency
    if frequency >= 10:
        f = 5
    elif frequency >= 5:
        f = 4
    elif frequency >= 3:
        f = 3
    elif frequency >= 2:
        f = 2
    else:
        f = 1

    # Monetary
    if monetary >= 10000:
        m = 5
    elif monetary >= 5000:
        m = 4
    elif monetary >= 2000:
        m = 3
    elif monetary >= 500:
        m = 2
    else:
        m = 1

    total = r + f + m
    if total >= 13:
        segment = "VIP"
        color = "success"
        icon = "👑"
    elif total >= 10:
        segment = "Regular"
        color = "primary"
        icon = "⭐"
    elif total >= 7:
        segment = "Occasional"
        color = "info"
        icon = "🔄"
    elif r <= 2 and f >= 2:
        segment = "At-Risk"
        color = "warning"
        icon = "⚠️"
    else:
        segment = "One-time"
        color = "secondary"
        icon = "👤"

    return {"r": r, "f": f, "m": m, "total": total,
            "segment": segment, "color": color, "icon": icon}


def segment_customers() -> dict:
    """Segment all known customers using RFM analysis."""
    customers = db.session.execute(db.select(Customer).order_by(Customer.visit_count.desc())).scalars().all()
    today = date.today()
    segmented = []

    for c in customers:
        # Get sales linked to this customer name
        sales = db.session.execute(
            db.select(Sale)
            .where(db.func.lower(Sale.customer_name) == c.name.lower())
            .order_by(Sale.sale_date.desc())
        ).scalars().all()

        if not sales:
            continue

        last_sale = sales[0].sale_date.date() if sales[0].sale_date else today
        recency = (today - last_sale).days
        frequency = len(sales)
        monetary = sum(float(s.total_amount) for s in sales)
        avg_order = monetary / frequency if frequency else 0

        rfm = _rfm_score(recency, frequency, monetary)

        # Discount recommendation
        if rfm["segment"] == "VIP":
            discount_rec = "Offer 5-10% loyalty discount"
        elif rfm["segment"] == "At-Risk":
            discount_rec = "Send win-back offer: 15% discount"
        elif rfm["segment"] == "Regular":
            discount_rec = "Reward with occasional 5% discount"
        else:
            discount_rec = "No special discount needed"

        segmented.append({
            "id": c.id,
            "name": c.name,
            "phone": c.phone,
            "address": c.address,
            "segment": rfm["segment"],
            "segment_color": rfm["color"],
            "segment_icon": rfm["icon"],
            "rfm_score": rfm["total"],
            "recency_days": recency,
            "frequency": frequency,
            "total_spent": round(monetary, 2),
            "avg_order_value": round(avg_order, 2),
            "last_visit": str(last_sale),
            "discount_recommendation": discount_rec,
        })

    # Summary
    from collections import Counter
    seg_counts = Counter(c["segment"] for c in segmented)

    return {
        "total_customers": len(segmented),
        "segments": dict(seg_counts),
        "customers": segmented,
        "top_vip": [c for c in segmented if c["segment"] == "VIP"][:5],
        "at_risk": [c for c in segmented if c["segment"] == "At-Risk"],
        "insight": f"{seg_counts.get('VIP', 0)} VIP customers generating most revenue. "
                   f"{seg_counts.get('At-Risk', 0)} customers at risk of churning.",
    }
