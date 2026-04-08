"""Customer Intelligence Module — Features 6-14

6.  Top Regular Customer Detection (Platinum/Gold/Silver tiers)
7.  Customer Behavior Analysis
8.  Personalized Recommendation Engine
9.  Customer Churn Prediction
10. Customer Lifetime Value (CLV)
11. Smart Loyalty & Offer Engine
12. Product Affinity Analysis
13. Smart Combo Generator
14. Customer Profitability Analysis
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean

from sqlalchemy import and_, func

from ..extensions import db
from ..models.customer import Customer
from ..models.product import Product
from ..models.sale import Sale, SaleItem


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_customer_sales(customer_name: str) -> list:
    return db.session.execute(
        db.select(Sale)
        .where(func.lower(Sale.customer_name) == customer_name.lower())
        .order_by(Sale.sale_date.desc())
    ).scalars().all()


def _all_named_customers() -> list[Customer]:
    return db.session.execute(
        db.select(Customer).order_by(Customer.visit_count.desc())
    ).scalars().all()


# ── 6. Top Regular Customer Detection ────────────────────────────────────────

def tier_customers() -> dict:
    """Rank and tier all customers: Platinum / Gold / Silver / Bronze."""
    customers = _all_named_customers()
    today = date.today()
    tiered = []

    for c in customers:
        sales = _get_customer_sales(c.name)
        if not sales:
            continue

        total_spent = sum(float(s.total_amount) for s in sales)
        frequency = len(sales)
        last_sale = sales[0].sale_date.date() if sales[0].sale_date else today
        recency_days = (today - last_sale).days
        avg_order = total_spent / frequency if frequency else 0

        # Composite score (weighted)
        monetary_score = min(50, total_spent / 1000 * 10)
        frequency_score = min(30, frequency * 3)
        recency_score = max(0, 20 - recency_days * 0.2)
        total_score = monetary_score + frequency_score + recency_score

        if total_score >= 70:
            tier, color, icon = "Platinum", "primary", "💎"
        elif total_score >= 50:
            tier, color, icon = "Gold", "warning", "🥇"
        elif total_score >= 30:
            tier, color, icon = "Silver", "secondary", "🥈"
        else:
            tier, color, icon = "Bronze", "danger", "🥉"

        tiered.append({
            "id": c.id,
            "name": c.name,
            "phone": c.phone,
            "tier": tier,
            "tier_color": color,
            "tier_icon": icon,
            "total_spent": round(total_spent, 2),
            "frequency": frequency,
            "avg_order": round(avg_order, 2),
            "recency_days": recency_days,
            "last_visit": str(last_sale),
            "score": round(total_score, 1),
        })

    tiered.sort(key=lambda x: x["score"], reverse=True)

    from collections import Counter
    tier_counts = Counter(c["tier"] for c in tiered)

    return {
        "total": len(tiered),
        "tier_counts": dict(tier_counts),
        "customers": tiered,
        "platinum": [c for c in tiered if c["tier"] == "Platinum"],
        "gold": [c for c in tiered if c["tier"] == "Gold"],
        "silver": [c for c in tiered if c["tier"] == "Silver"],
    }


# ── 7. Customer Behavior Analysis ────────────────────────────────────────────

def customer_behavior(customer_name: str) -> dict:
    """Analyze buying patterns, preferences, intervals, seasonal behavior."""
    sales = _get_customer_sales(customer_name)
    if not sales:
        return {"error": "No sales data for this customer."}

    today = date.today()
    total_spent = sum(float(s.total_amount) for s in sales)
    frequency = len(sales)

    # Purchase intervals
    dates = sorted([s.sale_date.date() for s in sales if s.sale_date])
    intervals = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
    avg_interval = round(mean(intervals), 1) if intervals else None

    # Product preferences
    product_counts = defaultdict(lambda: {"qty": 0, "revenue": 0.0, "name": ""})
    for s in sales:
        for item in s.items:
            pid = item.product_id
            product_counts[pid]["qty"] += item.quantity
            product_counts[pid]["revenue"] += float(item.subtotal)
            if item.product:
                product_counts[pid]["name"] = item.product.name

    top_products = sorted(product_counts.items(), key=lambda x: x[1]["qty"], reverse=True)[:5]
    preferences = [{"product_id": pid, "name": d["name"], "qty": d["qty"],
                    "revenue": round(d["revenue"], 2)} for pid, d in top_products]

    # Seasonal behavior (month distribution)
    month_dist = defaultdict(int)
    for s in sales:
        if s.sale_date:
            month_dist[s.sale_date.strftime("%B")] += 1
    peak_month = max(month_dist, key=month_dist.get) if month_dist else None

    # Day of week preference
    dow_dist = defaultdict(int)
    for s in sales:
        if s.sale_date:
            dow_dist[s.sale_date.strftime("%A")] += 1
    peak_day = max(dow_dist, key=dow_dist.get) if dow_dist else None

    # Payment mode preference
    pm_dist = defaultdict(int)
    for s in sales:
        pm_dist[s.payment_mode or "cash"] += 1
    preferred_payment = max(pm_dist, key=pm_dist.get) if pm_dist else "cash"

    return {
        "customer_name": customer_name,
        "total_spent": round(total_spent, 2),
        "frequency": frequency,
        "avg_order_value": round(total_spent / frequency, 2) if frequency else 0,
        "avg_purchase_interval_days": avg_interval,
        "top_products": preferences,
        "peak_month": peak_month,
        "peak_day": peak_day,
        "preferred_payment": preferred_payment,
        "first_purchase": str(dates[0]) if dates else None,
        "last_purchase": str(dates[-1]) if dates else None,
        "month_distribution": dict(month_dist),
        "day_distribution": dict(dow_dist),
    }


# ── 8. Personalized Recommendation Engine ────────────────────────────────────

def personalized_recommendations(customer_name: str) -> dict:
    """Generate product suggestions, combos, upsell/cross-sell."""
    behavior = customer_behavior(customer_name)
    if "error" in behavior:
        return behavior

    bought_ids = {p["product_id"] for p in behavior["top_products"]}
    top_categories = set()

    for pid in bought_ids:
        p = db.session.get(Product, pid)
        if p and p.category:
            top_categories.add(p.category)

    # Products in same categories not yet bought
    cross_sell = db.session.execute(
        db.select(Product)
        .where(Product.category.in_(top_categories))
        .where(Product.id.notin_(bought_ids))
        .where(Product.quantity > 0)
        .order_by(Product.selling_price.desc())
        .limit(5)
    ).scalars().all()

    # Upsell: premium products in same category
    upsell = []
    for pid in list(bought_ids)[:3]:
        p = db.session.get(Product, pid)
        if p:
            premium = db.session.execute(
                db.select(Product)
                .where(Product.category == p.category)
                .where(Product.selling_price > p.selling_price)
                .where(Product.quantity > 0)
                .order_by(Product.selling_price.asc())
                .limit(1)
            ).scalar_one_or_none()
            if premium:
                upsell.append({
                    "current": p.name,
                    "upgrade_to": premium.name,
                    "price_diff": round(float(premium.selling_price) - float(p.selling_price), 2),
                })

    # Combo suggestions from affinity
    affinity = product_affinity_analysis()
    combos = []
    for pair in affinity.get("top_pairs", [])[:3]:
        if any(pid in bought_ids for pid in [pair["product_a_id"], pair["product_b_id"]]):
            combos.append(pair)

    return {
        "customer_name": customer_name,
        "cross_sell": [{"id": p.id, "name": p.name, "price": float(p.selling_price),
                        "category": p.category} for p in cross_sell],
        "upsell": upsell,
        "combo_suggestions": combos,
        "based_on": behavior["top_products"][:3],
    }


# ── 9. Customer Churn Prediction ──────────────────────────────────────────────

def churn_prediction() -> dict:
    """Detect inactive customers and decreasing purchase frequency."""
    customers = _all_named_customers()
    today = date.today()
    at_risk = []
    churned = []

    for c in customers:
        sales = _get_customer_sales(c.name)
        if not sales:
            continue

        frequency = len(sales)
        last_sale = sales[0].sale_date.date() if sales[0].sale_date else today
        recency = (today - last_sale).days

        # Check frequency trend: compare last 3 months vs previous 3 months
        three_months_ago = today - timedelta(days=90)
        six_months_ago = today - timedelta(days=180)

        recent_count = sum(1 for s in sales if s.sale_date and s.sale_date.date() >= three_months_ago)
        prev_count = sum(1 for s in sales
                         if s.sale_date and six_months_ago <= s.sale_date.date() < three_months_ago)

        declining = prev_count > 0 and recent_count < prev_count * 0.5

        if recency > 90:
            churned.append({
                "name": c.name, "phone": c.phone,
                "days_inactive": recency,
                "total_spent": sum(float(s.total_amount) for s in sales),
                "risk": "churned",
                "action": "Win-back campaign with 20% discount",
            })
        elif recency > 45 or declining:
            at_risk.append({
                "name": c.name, "phone": c.phone,
                "days_inactive": recency,
                "frequency_drop": f"{prev_count} → {recent_count} orders",
                "total_spent": sum(float(s.total_amount) for s in sales),
                "risk": "at_risk",
                "action": "Send retention offer: 10% discount",
            })

    return {
        "churned_count": len(churned),
        "at_risk_count": len(at_risk),
        "churned": sorted(churned, key=lambda x: x["days_inactive"], reverse=True),
        "at_risk": sorted(at_risk, key=lambda x: x["days_inactive"], reverse=True),
        "insight": (
            f"{len(churned)} customers churned (90+ days inactive). "
            f"{len(at_risk)} at risk of churning."
        ),
    }


# ── 10. Customer Lifetime Value (CLV) ─────────────────────────────────────────

def customer_lifetime_value(customer_name: str) -> dict:
    """Calculate CLV and predict long-term value."""
    sales = _get_customer_sales(customer_name)
    if not sales:
        return {"error": "No data."}

    today = date.today()
    total_spent = sum(float(s.total_amount) for s in sales)
    frequency = len(sales)

    dates = sorted([s.sale_date.date() for s in sales if s.sale_date])
    if len(dates) >= 2:
        lifespan_days = (dates[-1] - dates[0]).days
        purchase_rate = frequency / max(lifespan_days / 30, 1)  # per month
    else:
        lifespan_days = 0
        purchase_rate = frequency

    avg_order = total_spent / frequency if frequency else 0

    # Simple CLV = avg_order * purchase_rate * 12 months * 3 years
    predicted_annual = avg_order * purchase_rate * 12
    predicted_3yr = predicted_annual * 3

    # Gross margin assumption 25%
    clv_net = predicted_3yr * 0.25

    return {
        "customer_name": customer_name,
        "historical_revenue": round(total_spent, 2),
        "avg_order_value": round(avg_order, 2),
        "purchase_frequency_per_month": round(purchase_rate, 2),
        "customer_lifespan_days": lifespan_days,
        "predicted_annual_revenue": round(predicted_annual, 2),
        "predicted_3yr_revenue": round(predicted_3yr, 2),
        "estimated_net_clv": round(clv_net, 2),
        "tier": "High Value" if clv_net > 5000 else "Medium Value" if clv_net > 1000 else "Low Value",
    }


# ── 11. Smart Loyalty & Offer Engine ─────────────────────────────────────────

def loyalty_offers(customer_name: str) -> dict:
    """Generate targeted discounts, retention offers, festival promotions."""
    behavior = customer_behavior(customer_name)
    if "error" in behavior:
        return behavior

    sales = _get_customer_sales(customer_name)
    total_spent = sum(float(s.total_amount) for s in sales)
    frequency = len(sales)
    recency = behavior.get("recency_days", 999) if "recency_days" not in behavior else (
        (date.today() - date.fromisoformat(behavior["last_purchase"])).days
        if behavior.get("last_purchase") else 999
    )

    offers = []

    # Loyalty discount based on total spend
    if total_spent >= 10000:
        offers.append({"type": "loyalty", "offer": "10% discount on next purchase",
                       "reason": f"Spent NPR {total_spent:,.0f} total", "priority": 1})
    elif total_spent >= 5000:
        offers.append({"type": "loyalty", "offer": "5% discount on next purchase",
                       "reason": f"Valued customer — NPR {total_spent:,.0f} spent", "priority": 2})

    # Retention offer
    if recency > 30:
        offers.append({"type": "retention", "offer": "15% win-back discount",
                       "reason": f"Not visited in {recency} days", "priority": 1})

    # Frequency reward
    if frequency >= 10:
        offers.append({"type": "frequency", "offer": "Free item on next purchase",
                       "reason": f"{frequency} purchases — loyal customer", "priority": 2})

    # Festival promotions (based on current month)
    month = date.today().month
    festivals = {
        10: "Dashain Special: 8% off on all items",
        11: "Tihar Offer: Buy 2 Get 1 Free on selected items",
        12: "New Year Special: 12% discount",
        1: "New Year Offer: 10% off",
        8: "Janai Purnima Special: 5% off",
    }
    if month in festivals:
        offers.append({"type": "festival", "offer": festivals[month],
                       "reason": "Festival season promotion", "priority": 3})

    # Product-specific offer based on preferences
    if behavior.get("top_products"):
        top = behavior["top_products"][0]
        offers.append({"type": "product", "offer": f"5% off on {top['name']}",
                       "reason": "Your most purchased product", "priority": 3})

    offers.sort(key=lambda x: x["priority"])
    return {
        "customer_name": customer_name,
        "offers": offers,
        "total_offers": len(offers),
        "best_offer": offers[0] if offers else None,
    }


# ── 12. Product Affinity Analysis ─────────────────────────────────────────────

def product_affinity_analysis(min_support: int = 2) -> dict:
    """Find products frequently bought together (market basket analysis)."""
    # Get all sales with multiple items
    sales = db.session.execute(
        db.select(Sale).join(SaleItem, SaleItem.sale_id == Sale.id)
        .group_by(Sale.id)
        .having(func.count(SaleItem.id) >= 2)
    ).scalars().all()

    pair_counts = defaultdict(int)
    product_counts = defaultdict(int)

    for sale in sales:
        items = [item.product_id for item in sale.items]
        for pid in items:
            product_counts[pid] += 1
        # Count pairs
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                pair = tuple(sorted([items[i], items[j]]))
                pair_counts[pair] += 1

    # Filter by minimum support
    top_pairs = [(pair, count) for pair, count in pair_counts.items() if count >= min_support]
    top_pairs.sort(key=lambda x: x[1], reverse=True)

    result_pairs = []
    for (pid_a, pid_b), count in top_pairs[:15]:
        pa = db.session.get(Product, pid_a)
        pb = db.session.get(Product, pid_b)
        if pa and pb:
            # Confidence: P(B|A) = count(A∩B) / count(A)
            confidence = count / product_counts[pid_a] if product_counts[pid_a] else 0
            result_pairs.append({
                "product_a_id": pid_a,
                "product_a": pa.name,
                "product_b_id": pid_b,
                "product_b": pb.name,
                "co_purchase_count": count,
                "confidence": round(confidence * 100, 1),
                "combined_price": float(pa.selling_price) + float(pb.selling_price),
            })

    return {
        "total_pairs_found": len(result_pairs),
        "top_pairs": result_pairs,
        "insight": (
            f"Top combo: {result_pairs[0]['product_a']} + {result_pairs[0]['product_b']} "
            f"(bought together {result_pairs[0]['co_purchase_count']} times)"
        ) if result_pairs else "Not enough data for affinity analysis.",
    }


# ── 13. Smart Combo Generator ─────────────────────────────────────────────────

def generate_combos() -> dict:
    """Auto-generate bundle offers with combo pricing."""
    affinity = product_affinity_analysis()
    combos = []

    for pair in affinity.get("top_pairs", [])[:10]:
        pa = db.session.get(Product, pair["product_a_id"])
        pb = db.session.get(Product, pair["product_b_id"])
        if not pa or not pb:
            continue

        original_price = float(pa.selling_price) + float(pb.selling_price)
        # Discount: 5-10% based on confidence
        discount_pct = min(10, max(5, pair["confidence"] / 10))
        combo_price = round(original_price * (1 - discount_pct / 100), 2)
        savings = round(original_price - combo_price, 2)

        combos.append({
            "combo_name": f"{pa.name} + {pb.name} Bundle",
            "product_a": {"id": pa.id, "name": pa.name, "price": float(pa.selling_price)},
            "product_b": {"id": pb.id, "name": pb.name, "price": float(pb.selling_price)},
            "original_price": round(original_price, 2),
            "combo_price": combo_price,
            "discount_pct": round(discount_pct, 1),
            "savings": savings,
            "co_purchase_count": pair["co_purchase_count"],
            "confidence": pair["confidence"],
        })

    return {
        "total_combos": len(combos),
        "combos": combos,
        "insight": f"{len(combos)} smart combo bundles generated based on purchase patterns.",
    }


# ── 14. Customer Profitability Analysis ───────────────────────────────────────

def customer_profitability() -> dict:
    """Identify high-value and low-margin customer segments."""
    customers = _all_named_customers()
    analysis = []

    for c in customers:
        sales = _get_customer_sales(c.name)
        if not sales:
            continue

        total_revenue = sum(float(s.total_amount) for s in sales)
        total_discount = sum(float(s.discount_amount or 0) for s in sales)
        frequency = len(sales)

        # Calculate COGS for this customer
        cogs = 0.0
        for s in sales:
            for item in s.items:
                if item.product:
                    cogs += float(item.product.cost_price) * item.quantity

        gross_profit = total_revenue - cogs
        margin_pct = (gross_profit / total_revenue * 100) if total_revenue > 0 else 0

        if total_revenue >= 10000 and margin_pct >= 20:
            segment = "High Value"
            color = "success"
        elif total_revenue >= 5000:
            segment = "Medium Value"
            color = "primary"
        elif margin_pct < 10:
            segment = "Low Margin"
            color = "warning"
        else:
            segment = "Standard"
            color = "secondary"

        analysis.append({
            "name": c.name,
            "phone": c.phone,
            "total_revenue": round(total_revenue, 2),
            "total_discount": round(total_discount, 2),
            "cogs": round(cogs, 2),
            "gross_profit": round(gross_profit, 2),
            "margin_pct": round(margin_pct, 1),
            "frequency": frequency,
            "segment": segment,
            "segment_color": color,
        })

    analysis.sort(key=lambda x: x["gross_profit"], reverse=True)
    total_profit = sum(a["gross_profit"] for a in analysis)

    return {
        "total_customers": len(analysis),
        "total_profit_from_customers": round(total_profit, 2),
        "customers": analysis,
        "high_value": [a for a in analysis if a["segment"] == "High Value"],
        "low_margin": [a for a in analysis if a["segment"] == "Low Margin"],
        "insight": (
            f"Top {min(3, len(analysis))} customers generate "
            f"NPR {sum(a['gross_profit'] for a in analysis[:3]):,.0f} in profit."
        ) if analysis else "No customer data.",
    }
