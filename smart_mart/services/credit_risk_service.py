"""Customer Credit Risk Scoring Service.

Calculates a risk score for each customer based on their Udhar (credit) history:
- How many credit sales they've taken
- How quickly they pay back
- Outstanding balance
- Overdue credits

Risk Levels:
  🟢 SAFE    — score 70-100  (pays on time, low outstanding)
  🟡 WATCH   — score 40-69   (sometimes late, moderate outstanding)
  🔴 RISKY   — score 0-39    (frequently late/overdue, high outstanding)
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func

from ..extensions import db
from ..models.sale import Sale
from ..models.operations import CustomerCreditPayment


def _days_to_pay(sale: Sale) -> int | None:
    """How many days it took to fully collect a credit sale. None if still unpaid."""
    if not sale.credit_collected:
        return None
    last_payment = db.session.execute(
        db.select(func.max(CustomerCreditPayment.paid_at))
        .where(CustomerCreditPayment.sale_id == sale.id)
    ).scalar()
    if last_payment and sale.sale_date:
        return max(0, (last_payment.date() - sale.sale_date.date()).days)
    return None


def calculate_risk_score(customer_name: str) -> dict:
    """Calculate credit risk score for a customer. Returns score 0-100 and risk level."""
    today = date.today()

    # All credit sales for this customer
    credit_sales = db.session.execute(
        db.select(Sale)
        .where(
            func.lower(Sale.customer_name) == customer_name.strip().lower(),
            Sale.payment_mode == "credit"
        )
        .order_by(Sale.sale_date.desc())
    ).scalars().all()

    if not credit_sales:
        return {
            "customer_name": customer_name,
            "score": 100,
            "risk_level": "safe",
            "risk_label": "🟢 Safe",
            "risk_color": "success",
            "total_credit_sales": 0,
            "total_borrowed": 0.0,
            "total_outstanding": 0.0,
            "overdue_count": 0,
            "avg_days_to_pay": None,
            "paid_on_time_pct": 100,
            "summary": "No credit history. New customer.",
        }

    total_borrowed = sum(float(s.total_amount) for s in credit_sales)
    total_count = len(credit_sales)

    # Outstanding balance
    paid_amounts = {}
    for s in credit_sales:
        paid = db.session.execute(
            db.select(func.coalesce(func.sum(CustomerCreditPayment.amount), 0))
            .where(CustomerCreditPayment.sale_id == s.id)
        ).scalar() or 0
        paid_amounts[s.id] = float(paid)

    total_outstanding = sum(
        max(0, float(s.total_amount) - paid_amounts[s.id])
        for s in credit_sales
    )

    # Overdue count
    overdue_count = sum(
        1 for s in credit_sales
        if not s.credit_collected
        and s.credit_due_date
        and s.credit_due_date < today
    )

    # Unpaid with no due date set (open-ended Udhar)
    open_udhar = sum(
        1 for s in credit_sales
        if not s.credit_collected and not s.credit_due_date
    )

    # Payment speed for collected sales
    days_list = [_days_to_pay(s) for s in credit_sales if s.credit_collected]
    days_list = [d for d in days_list if d is not None]
    avg_days = round(sum(days_list) / len(days_list), 1) if days_list else None

    # On-time payment percentage (paid within 30 days)
    collected = [s for s in credit_sales if s.credit_collected]
    on_time = sum(1 for d in days_list if d <= 30)
    on_time_pct = round(on_time / len(days_list) * 100) if days_list else 100

    # ── Score calculation (0-100, higher = safer) ─────────────────────────
    score = 100

    # Deduct for outstanding balance ratio
    if total_borrowed > 0:
        outstanding_ratio = total_outstanding / total_borrowed
        score -= int(outstanding_ratio * 40)  # up to -40 for 100% outstanding

    # Deduct for overdue credits
    score -= overdue_count * 15  # -15 per overdue

    # Deduct for open-ended Udhar (no due date)
    score -= open_udhar * 5  # -5 per open Udhar

    # Deduct for slow payment
    if avg_days is not None:
        if avg_days > 60:
            score -= 20
        elif avg_days > 30:
            score -= 10
        elif avg_days > 14:
            score -= 5

    # Deduct for low on-time payment rate
    if on_time_pct < 50:
        score -= 15
    elif on_time_pct < 75:
        score -= 7

    # Bonus for good payment history
    if len(collected) >= 3 and on_time_pct >= 90 and total_outstanding == 0:
        score = min(100, score + 10)

    score = max(0, min(100, score))

    # ── Risk level ────────────────────────────────────────────────────────
    if score >= 70:
        risk_level = "safe"
        risk_label = "🟢 Safe"
        risk_color = "success"
    elif score >= 40:
        risk_level = "watch"
        risk_label = "🟡 Watch"
        risk_color = "warning"
    else:
        risk_level = "risky"
        risk_label = "🔴 Risky"
        risk_color = "danger"

    # ── Human-readable summary ────────────────────────────────────────────
    parts = []
    if total_outstanding > 0:
        parts.append(f"NPR {total_outstanding:,.0f} outstanding")
    if overdue_count > 0:
        parts.append(f"{overdue_count} overdue")
    if avg_days is not None:
        parts.append(f"avg {avg_days:.0f} days to pay")
    if on_time_pct < 100 and days_list:
        parts.append(f"{on_time_pct}% on time")
    summary = " · ".join(parts) if parts else "All credits cleared on time."

    return {
        "customer_name": customer_name,
        "score": score,
        "risk_level": risk_level,
        "risk_label": risk_label,
        "risk_color": risk_color,
        "total_credit_sales": total_count,
        "total_borrowed": round(total_borrowed, 2),
        "total_outstanding": round(total_outstanding, 2),
        "overdue_count": overdue_count,
        "open_udhar_count": open_udhar,
        "avg_days_to_pay": avg_days,
        "paid_on_time_pct": on_time_pct,
        "collected_count": len(collected),
        "summary": summary,
        "credit_sales": credit_sales,
        "paid_amounts": paid_amounts,
    }


def get_all_customer_risk_scores() -> list[dict]:
    """Get risk scores for all customers who have ever taken credit."""
    customer_names = db.session.execute(
        db.select(Sale.customer_name.distinct())
        .where(Sale.payment_mode == "credit", Sale.customer_name.isnot(None))
        .order_by(Sale.customer_name)
    ).scalars().all()

    scores = []
    for name in customer_names:
        if name and name.strip():
            scores.append(calculate_risk_score(name))

    # Sort: risky first, then watch, then safe; within each group by outstanding desc
    order = {"risky": 0, "watch": 1, "safe": 2}
    scores.sort(key=lambda x: (order.get(x["risk_level"], 9), -x["total_outstanding"]))
    return scores


def get_risk_summary() -> dict:
    """Quick summary counts for dashboard widget."""
    scores = get_all_customer_risk_scores()
    return {
        "total": len(scores),
        "safe": sum(1 for s in scores if s["risk_level"] == "safe"),
        "watch": sum(1 for s in scores if s["risk_level"] == "watch"),
        "risky": sum(1 for s in scores if s["risk_level"] == "risky"),
        "total_outstanding": sum(s["total_outstanding"] for s in scores),
    }
