"""Self-Learning AI Engine — retraining, drift detection, recommendations, feedback loop."""

from __future__ import annotations
import json
from datetime import date, datetime, timedelta, timezone
from statistics import mean, stdev
from sqlalchemy import func
from ..extensions import db
from ..models.ai_memory import (AIModelVersion, AIAlert, AIRecommendation,
                                  AIFeedbackLog, AIRetrainingLog)
from ..models.product import Product
from ..models.sale import Sale, SaleItem
from ..models.purchase import Purchase


def get_active_model(model_name: str):
    return db.session.execute(
        db.select(AIModelVersion)
        .filter_by(model_name=model_name, is_active=True)
        .order_by(AIModelVersion.version.desc())
    ).scalar_one_or_none()


def register_model_version(model_name: str, accuracy: float, data_points: int,
                            params: dict = None, notes: str = None) -> AIModelVersion:
    db.session.execute(
        db.update(AIModelVersion)
        .where(AIModelVersion.model_name == model_name)
        .values(is_active=False)
    )
    last = db.session.execute(
        db.select(func.max(AIModelVersion.version))
        .where(AIModelVersion.model_name == model_name)
    ).scalar() or 0
    model = AIModelVersion(
        model_name=model_name, version=last + 1,
        accuracy_score=accuracy, data_points_used=data_points,
        parameters=json.dumps(params or {}), is_active=True, notes=notes,
    )
    db.session.add(model)
    db.session.commit()
    return model


def retrain_all_models() -> dict:
    """Run full retraining cycle."""
    log = AIRetrainingLog(trigger="scheduled", status="running")
    db.session.add(log)
    db.session.commit()

    results = {}
    try:
        results["demand"] = _retrain_demand()
        results["anomaly"] = _retrain_anomaly_thresholds()
        log.status = "completed"
        log.completed_at = datetime.now(timezone.utc)
        log.models_retrained = json.dumps(list(results.keys()))
        log.summary = f"Retrained {len(results)} models successfully."
    except Exception as e:
        log.status = "failed"
        log.summary = str(e)
    db.session.commit()
    return {"status": log.status, "results": results, "log_id": log.id}


def _retrain_demand() -> dict:
    end = date.today()
    start = end - timedelta(days=90)
    rows = db.session.execute(
        db.select(func.date(Sale.sale_date).label("day"), func.sum(Sale.total_amount).label("total"))
        .where(func.date(Sale.sale_date) >= start)
        .group_by(func.date(Sale.sale_date))
        .order_by(func.date(Sale.sale_date))
    ).all()
    if len(rows) < 7:
        return {"status": "insufficient_data"}
    series = [float(r.total) for r in rows]
    n = len(series)
    avg = mean(series)
    sd = stdev(series) if n > 1 else 0
    x_mean = (n - 1) / 2
    num = sum((i - x_mean) * (series[i] - avg) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    slope = num / den if den else 0
    accuracy = max(0.0, min(1.0, 1 - (sd / avg))) if avg > 0 else 0.5
    params = {"slope": round(slope, 4), "avg": round(avg, 2), "std": round(sd, 2), "n": n}
    m = register_model_version("demand_forecast", round(accuracy, 4), n, params)
    return {"version": m.version, "accuracy": round(accuracy, 4), "data_points": n}


def _retrain_anomaly_thresholds() -> dict:
    end = date.today()
    start = end - timedelta(days=60)
    rows = db.session.execute(
        db.select(func.sum(Sale.total_amount).label("total"))
        .where(func.date(Sale.sale_date) >= start)
        .group_by(func.date(Sale.sale_date))
    ).all()
    if len(rows) < 5:
        return {"status": "insufficient_data"}
    series = [float(r.total) for r in rows]
    avg = mean(series)
    sd = stdev(series) if len(series) > 1 else avg * 0.2
    params = {
        "mean": round(avg, 2), "std": round(sd, 2),
        "upper_threshold": round(avg + 2 * sd, 2),
        "lower_threshold": round(max(0, avg - 2 * sd), 2),
    }
    m = register_model_version("anomaly_detection", 0.85, len(series), params)
    return {"version": m.version, "thresholds": params}


# ── SELF ISSUE DETECTION ──────────────────────────────────────────────────────

def run_self_detection() -> list[dict]:
    """Run all self-detection checks and store alerts."""
    alerts = []
    alerts.extend(_check_stock_fluctuations())
    alerts.extend(_check_profit_drops())
    alerts.extend(_check_sales_spikes())
    alerts.extend(_check_low_stock_critical())

    for a in alerts:
        existing = db.session.execute(
            db.select(AIAlert)
            .filter_by(alert_type=a["type"], entity_id=a.get("entity_id"), is_resolved=False)
        ).scalar_one_or_none()
        if not existing:
            alert = AIAlert(
                alert_type=a["type"], severity=a["severity"],
                title=a["title"], description=a["description"],
                entity_type=a.get("entity_type"), entity_id=a.get("entity_id"),
            )
            db.session.add(alert)
    db.session.commit()
    return alerts


def _check_stock_fluctuations() -> list[dict]:
    alerts = []
    products = db.session.execute(db.select(Product).where(Product.quantity == 0)).scalars().all()
    for p in products:
        alerts.append({
            "type": "stock_out", "severity": "critical",
            "title": f"OUT OF STOCK: {p.name}",
            "description": f"{p.name} (SKU: {p.sku}) has zero stock.",
            "entity_type": "product", "entity_id": p.id,
        })
    low = db.session.execute(
        db.select(Product).where(Product.quantity > 0).where(Product.quantity <= 5)
    ).scalars().all()
    for p in low:
        alerts.append({
            "type": "low_stock", "severity": "high",
            "title": f"Critical Low Stock: {p.name}",
            "description": f"Only {p.quantity} units remaining.",
            "entity_type": "product", "entity_id": p.id,
        })
    return alerts


def _check_profit_drops() -> list[dict]:
    today = date.today()
    yesterday = today - timedelta(days=1)
    week_ago = today - timedelta(days=7)

    today_rev = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) == today)
    ).scalar() or 0
    yesterday_rev = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) == yesterday)
    ).scalar() or 0

    alerts = []
    if float(yesterday_rev) > 0:
        drop = ((float(yesterday_rev) - float(today_rev)) / float(yesterday_rev)) * 100
        if drop > 50:
            alerts.append({
                "type": "revenue_drop", "severity": "high",
                "title": f"Revenue Drop: {drop:.0f}% vs yesterday",
                "description": f"Today: NPR {float(today_rev):,.0f} vs Yesterday: NPR {float(yesterday_rev):,.0f}",
                "entity_type": None, "entity_id": None,
            })
    return alerts


def _check_sales_spikes() -> list[dict]:
    model = get_active_model("anomaly_detection")
    if not model or not model.parameters:
        return []
    params = json.loads(model.parameters)
    upper = params.get("upper_threshold", 0)
    today_rev = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) == date.today())
    ).scalar() or 0
    alerts = []
    if float(today_rev) > upper > 0:
        alerts.append({
            "type": "sales_spike", "severity": "medium",
            "title": f"Unusual Sales Spike Detected",
            "description": f"Today's sales NPR {float(today_rev):,.0f} exceeds threshold NPR {upper:,.0f}.",
            "entity_type": None, "entity_id": None,
        })
    return alerts


def _check_low_stock_critical() -> list[dict]:
    from ..services.ai_engine import restock_recommendation
    products = db.session.execute(
        db.select(Product).where(Product.quantity <= 10).where(Product.quantity > 0)
    ).scalars().all()
    alerts = []
    for p in products:
        try:
            rec = restock_recommendation(p.id)
            if rec.get("urgency") == "critical":
                alerts.append({
                    "type": "restock_critical", "severity": "critical",
                    "title": f"Restock Now: {p.name}",
                    "description": f"Only {rec.get('days_of_stock_left', 0)} days of stock left.",
                    "entity_type": "product", "entity_id": p.id,
                })
        except Exception:
            pass
    return alerts


# ── RECOMMENDATION ENGINE ─────────────────────────────────────────────────────

def generate_recommendations() -> list[dict]:
    """Generate AI recommendations with confidence scores."""
    recs = []
    recs.extend(_pricing_recommendations())
    recs.extend(_restock_recommendations())
    recs.extend(_discount_recommendations())

    for r in recs:
        existing = db.session.execute(
            db.select(AIRecommendation)
            .filter_by(category=r["category"], entity_id=r.get("entity_id"), status="pending")
        ).scalar_one_or_none()
        if not existing:
            rec = AIRecommendation(
                category=r["category"], title=r["title"],
                reason=r["reason"], expected_impact=r["expected_impact"],
                confidence_score=r["confidence"], entity_type=r.get("entity_type"),
                entity_id=r.get("entity_id"), action_data=json.dumps(r.get("action_data", {})),
            )
            db.session.add(rec)
    db.session.commit()
    return recs


def _pricing_recommendations() -> list[dict]:
    recs = []
    products = db.session.execute(db.select(Product)).scalars().all()
    for p in products:
        if float(p.selling_price) <= 0:
            continue
        margin = ((float(p.selling_price) - float(p.cost_price)) / float(p.selling_price)) * 100
        if margin < 10:
            suggested = float(p.cost_price) / 0.80
            recs.append({
                "category": "pricing", "entity_type": "product", "entity_id": p.id,
                "title": f"Increase price of {p.name}",
                "reason": f"Current margin {margin:.1f}% is below 20% target.",
                "expected_impact": f"Increase profit by NPR {(suggested - float(p.selling_price)):.0f} per unit",
                "confidence": 0.85,
                "action_data": {"product_id": p.id, "suggested_price": round(suggested, 2)},
            })
    return recs[:5]


def _restock_recommendations() -> list[dict]:
    from ..services.ai_engine import restock_recommendation
    recs = []
    products = db.session.execute(
        db.select(Product).where(Product.quantity <= 15).order_by(Product.quantity)
    ).scalars().all()
    for p in products[:5]:
        try:
            rec = restock_recommendation(p.id)
            if rec.get("should_restock"):
                recs.append({
                    "category": "restock", "entity_type": "product", "entity_id": p.id,
                    "title": f"Restock {p.name}",
                    "reason": f"Only {rec['days_of_stock_left']} days of stock remaining.",
                    "expected_impact": f"Prevent stockout. Order {rec['recommended_qty']} units.",
                    "confidence": 0.90,
                    "action_data": {"product_id": p.id, "qty": rec["recommended_qty"]},
                })
        except Exception:
            pass
    return recs


def _discount_recommendations() -> list[dict]:
    from ..services.ai_trend_analyzer import dead_stock_analysis
    recs = []
    dead = dead_stock_analysis(30)
    for item in dead.get("items", [])[:3]:
        recs.append({
            "category": "discount", "entity_type": "product", "entity_id": item["id"],
            "title": f"Apply discount to {item['name']}",
            "reason": f"No sales in {item.get('days_since_sale', 30)}+ days. Stock value: NPR {item['stock_value']:,.0f}.",
            "expected_impact": "Clear dead stock, recover NPR " + f"{item['stock_value'] * 0.7:,.0f}",
            "confidence": 0.75,
            "action_data": {"product_id": item["id"], "suggested_discount_pct": 20},
        })
    return recs


# ── FEEDBACK LOOP ─────────────────────────────────────────────────────────────

def record_feedback(recommendation_id: int, action: str, notes: str = None) -> dict:
    """Record feedback on a recommendation."""
    rec = db.session.get(AIRecommendation, recommendation_id)
    if not rec:
        return {"error": "Recommendation not found"}
    rec.status = action
    rec.feedback_note = notes
    rec.acted_at = datetime.now(timezone.utc)
    log = AIFeedbackLog(recommendation_id=recommendation_id, action=action, notes=notes)
    db.session.add(log)
    db.session.commit()
    return {"status": "recorded", "recommendation_id": recommendation_id, "action": action}


def get_feedback_stats() -> dict:
    """Get feedback statistics for learning loop analysis."""
    from collections import Counter
    logs = db.session.execute(db.select(AIFeedbackLog)).scalars().all()
    counts = Counter(l.action for l in logs)
    total = len(logs)
    acceptance_rate = counts.get("accepted", 0) / total if total else 0
    return {
        "total_feedback": total,
        "accepted": counts.get("accepted", 0),
        "rejected": counts.get("rejected", 0),
        "modified": counts.get("modified", 0),
        "acceptance_rate": round(acceptance_rate, 3),
        "insight": (
            "AI recommendations are well-calibrated." if acceptance_rate > 0.7
            else "Consider reviewing AI recommendation thresholds." if acceptance_rate < 0.4
            else "Moderate acceptance rate. Monitor trends."
        ),
    }


# ── DRIFT DETECTION ───────────────────────────────────────────────────────────

def detect_model_drift() -> dict:
    """Detect if model accuracy has drifted and retraining is needed."""
    model = get_active_model("demand_forecast")
    if not model:
        return {"drift_detected": False, "message": "No model trained yet."}

    days_since_training = (datetime.now(timezone.utc) - model.trained_at).days
    drift_signals = []

    if days_since_training > 7:
        drift_signals.append(f"Model is {days_since_training} days old (threshold: 7 days)")

    if model.accuracy_score and model.accuracy_score < 0.6:
        drift_signals.append(f"Model accuracy {model.accuracy_score:.1%} below 60% threshold")

    drift_detected = len(drift_signals) > 0
    return {
        "drift_detected": drift_detected,
        "model_name": model.model_name,
        "model_version": model.version,
        "model_age_days": days_since_training,
        "accuracy": model.accuracy_score,
        "drift_signals": drift_signals,
        "action": "retrain" if drift_detected else "none",
        "message": "Retraining recommended." if drift_detected else "Model is current.",
    }


# ── SELF-HEALING DATA ─────────────────────────────────────────────────────────

def self_heal_data() -> dict:
    """Detect and report data quality issues."""
    issues = []

    # Duplicate sales (same invoice number)
    from ..models.sale import Sale
    dupes = db.session.execute(
        db.select(Sale.invoice_number, func.count(Sale.id).label("cnt"))
        .where(Sale.invoice_number.isnot(None))
        .group_by(Sale.invoice_number)
        .having(func.count(Sale.id) > 1)
    ).all()
    for d in dupes:
        issues.append({
            "type": "duplicate_invoice",
            "severity": "high",
            "description": f"Invoice {d.invoice_number} appears {d.cnt} times.",
            "auto_fixable": False,
        })

    # Products with missing cost price
    from ..models.product import Product
    missing_cost = db.session.execute(
        db.select(func.count(Product.id)).where(Product.cost_price == 0)
    ).scalar() or 0
    if missing_cost > 0:
        issues.append({
            "type": "missing_cost_price",
            "severity": "medium",
            "description": f"{missing_cost} products have zero cost price.",
            "auto_fixable": False,
        })

    # Sales with zero amount
    zero_sales = db.session.execute(
        db.select(func.count(Sale.id)).where(Sale.total_amount == 0)
    ).scalar() or 0
    if zero_sales > 0:
        issues.append({
            "type": "zero_amount_sales",
            "severity": "medium",
            "description": f"{zero_sales} sales have zero total amount.",
            "auto_fixable": True,
        })

    return {
        "total_issues": len(issues),
        "issues": issues,
        "health_score": max(0, 100 - len(issues) * 10),
        "status": "healthy" if not issues else "needs_attention",
    }
