"""Customer Credit Risk Scoring Service.

Calculates a risk score (0-100) for each customer based on their Udhar (credit) history
and persists results in the customer_risk_scores table.

Risk Tiers (spec-aligned):
  🟢 Safe     — score 0-39   (low risk)
  🟡 Moderate — score 40-69  (moderate risk)
  🔴 Risky    — score 70-100 (high risk)

Score is inverted from the old implementation: higher score = MORE risk.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func

from ..extensions import db
from ..models.sale import Sale
from ..models.operations import CustomerCreditPayment
from ..models.customer_risk_score import CustomerRiskScore


# ── Tier helpers ──────────────────────────────────────────────────────────────

TIER_LABELS = {
    "safe": ("🟢 Safe", "success"),
    "moderate": ("🟡 Moderate", "warning"),
    "risky": ("🔴 Risky", "danger"),
}

TIER_ORDER = {"risky": 0, "moderate": 1, "safe": 2}


def _score_to_tier(score: int) -> str:
    if score <= 39:
        return "safe"
    if score <= 69:
        return "moderate"
    return "risky"


def _days_to_pay(sale: Sale) -> int | None:
    """Days from sale_date to last payment. None if still unpaid."""
    if not sale.credit_collected:
        return None
    last_payment = db.session.execute(
        db.select(func.max(CustomerCreditPayment.paid_at))
        .where(CustomerCreditPayment.sale_id == sale.id)
    ).scalar()
    if last_payment and sale.sale_date:
        return max(0, (last_payment.date() - sale.sale_date.date()).days)
    return None


# ── Core computation ──────────────────────────────────────────────────────────

def _compute_raw(customer_name: str) -> dict:
    """Compute risk score from DB data. Returns a dict with all metrics."""
    today = date.today()

    credit_sales = db.session.execute(
        db.select(Sale)
        .where(
            func.lower(Sale.customer_name) == customer_name.strip().lower(),
            Sale.payment_mode == "credit",
        )
        .order_by(Sale.sale_date.desc())
    ).scalars().all()

    if not credit_sales:
        return {
            "customer_name": customer_name,
            "score": 0,
            "risk_tier": "safe",
            "total_credit_sales": 0,
            "total_borrowed": 0.0,
            "total_outstanding": 0.0,
            "overdue_count": 0,
            "avg_days_to_pay": None,
            "on_time_rate": 1.0,
            "paid_on_time_pct": 100,
            "summary": "No credit history.",
        }

    total_borrowed = sum(float(s.total_amount) for s in credit_sales)

    # Outstanding per sale
    paid_amounts: dict[int, float] = {}
    for s in credit_sales:
        paid = db.session.execute(
            db.select(func.coalesce(func.sum(CustomerCreditPayment.amount), 0))
            .where(CustomerCreditPayment.sale_id == s.id)
        ).scalar() or 0
        paid_amounts[s.id] = float(paid)

    total_outstanding = sum(
        max(0.0, float(s.total_amount) - paid_amounts[s.id]) for s in credit_sales
    )

    # Active overdue (unpaid + past due date)
    overdue_count = sum(
        1 for s in credit_sales
        if not s.credit_collected and s.credit_due_date and s.credit_due_date < today
    )

    # Average days overdue for overdue sales
    overdue_days = []
    for s in credit_sales:
        if not s.credit_collected and s.credit_due_date and s.credit_due_date < today:
            overdue_days.append((today - s.credit_due_date).days)
    avg_days_overdue = sum(overdue_days) / len(overdue_days) if overdue_days else 0.0

    # On-time rate: settled sales paid on or before due date
    settled = [s for s in credit_sales if s.credit_collected]
    on_time = 0
    for s in settled:
        d = _days_to_pay(s)
        if d is not None:
            if s.credit_due_date:
                # paid before or on due date
                last_pay = db.session.execute(
                    db.select(func.max(CustomerCreditPayment.paid_at))
                    .where(CustomerCreditPayment.sale_id == s.id)
                ).scalar()
                if last_pay and last_pay.date() <= s.credit_due_date:
                    on_time += 1
            else:
                # no due date — treat as on-time if paid within 30 days
                if d <= 30:
                    on_time += 1

    on_time_rate = on_time / len(settled) if settled else 1.0
    on_time_pct = round(on_time_rate * 100)

    days_list = [_days_to_pay(s) for s in settled]
    days_list = [d for d in days_list if d is not None]
    avg_days_to_pay = round(sum(days_list) / len(days_list), 1) if days_list else None

    # ── Weighted score (0-100, higher = MORE risk) ────────────────────────
    # Factor 1: On-time rate (weight 40%) — lower rate → higher risk
    f1 = (1.0 - on_time_rate) * 100  # 0 = perfect, 100 = never on time

    # Factor 2: Overdue count (weight 25%) — normalised to 0-100 (cap at 5)
    f2 = min(overdue_count / 5.0, 1.0) * 100

    # Factor 3: Outstanding ratio (weight 20%) — outstanding / borrowed
    f3 = (total_outstanding / total_borrowed * 100) if total_borrowed > 0 else 0.0

    # Factor 4: Average days overdue (weight 15%) — normalised (cap at 90 days)
    f4 = min(avg_days_overdue / 90.0, 1.0) * 100

    score = round(f1 * 0.40 + f2 * 0.25 + f3 * 0.20 + f4 * 0.15)
    score = max(0, min(100, score))

    risk_tier = _score_to_tier(score)

    # Summary
    parts = []
    if total_outstanding > 0:
        parts.append(f"NPR {total_outstanding:,.0f} outstanding")
    if overdue_count > 0:
        parts.append(f"{overdue_count} overdue")
    if avg_days_to_pay is not None:
        parts.append(f"avg {avg_days_to_pay:.0f} days to pay")
    if on_time_pct < 100 and days_list:
        parts.append(f"{on_time_pct}% on time")
    summary = " · ".join(parts) if parts else "All credits cleared on time."

    return {
        "customer_name": customer_name,
        "score": score,
        "risk_tier": risk_tier,
        "total_credit_sales": len(credit_sales),
        "total_borrowed": round(total_borrowed, 2),
        "total_outstanding": round(total_outstanding, 2),
        "overdue_count": overdue_count,
        "avg_days_to_pay": avg_days_to_pay,
        "on_time_rate": on_time_rate,
        "paid_on_time_pct": on_time_pct,
        "summary": summary,
        "credit_sales": credit_sales,
        "paid_amounts": paid_amounts,
    }


def _persist(raw: dict) -> CustomerRiskScore:
    """Upsert the CustomerRiskScore row for this customer."""
    row = db.session.execute(
        db.select(CustomerRiskScore)
        .where(CustomerRiskScore.customer_name == raw["customer_name"])
    ).scalar_one_or_none()

    if row is None:
        row = CustomerRiskScore(customer_name=raw["customer_name"])
        db.session.add(row)

    row.risk_score = raw["score"]
    row.risk_tier = raw["risk_tier"]
    row.last_computed_at = datetime.now(timezone.utc)
    db.session.commit()
    return row


# ── Public API ────────────────────────────────────────────────────────────────

def calculate_risk_score(customer_name: str) -> dict:
    """Compute, persist, and return full risk data for a customer."""
    raw = _compute_raw(customer_name)
    row = _persist(raw)

    label, color = TIER_LABELS[row.effective_tier]
    raw.update({
        "risk_level": row.effective_tier,   # backward-compat alias
        "risk_label": label,
        "risk_color": color,
        "has_override": row.has_override,
        "override_tier": row.override_tier,
    })
    return raw


def get_risk_for_customer(customer_name: str) -> dict:
    """Return stored risk data (fast path). Falls back to on-demand computation."""
    row = db.session.execute(
        db.select(CustomerRiskScore)
        .where(CustomerRiskScore.customer_name == customer_name)
    ).scalar_one_or_none()

    if row is None:
        return calculate_risk_score(customer_name)

    label, color = TIER_LABELS[row.effective_tier]
    return {
        "customer_name": customer_name,
        "score": row.risk_score,
        "risk_tier": row.risk_tier,
        "risk_level": row.effective_tier,
        "risk_label": label,
        "risk_color": color,
        "has_override": row.has_override,
        "override_tier": row.override_tier,
        "total_outstanding": 0.0,  # lightweight — no full recompute
    }


def get_all_customer_risk_scores() -> list[dict]:
    """Compute and return risk scores for all credit customers."""
    customer_names = db.session.execute(
        db.select(Sale.customer_name.distinct())
        .where(Sale.payment_mode == "credit", Sale.customer_name.isnot(None))
        .order_by(Sale.customer_name)
    ).scalars().all()

    scores = []
    for name in customer_names:
        if name and name.strip():
            scores.append(calculate_risk_score(name))

    scores.sort(key=lambda x: (TIER_ORDER.get(x["risk_level"], 9), -x["total_outstanding"]))
    return scores


def recalculate_all() -> int:
    """Recompute risk scores for all credit customers. Preserves overrides. Returns count updated."""
    customer_names = db.session.execute(
        db.select(Sale.customer_name.distinct())
        .where(Sale.payment_mode == "credit", Sale.customer_name.isnot(None))
    ).scalars().all()

    count = 0
    for name in customer_names:
        if name and name.strip():
            raw = _compute_raw(name)
            _persist(raw)
            count += 1
    return count


def set_override(customer_name: str, override_tier: str | None, admin_user_id: int) -> CustomerRiskScore:
    """Set or clear an admin override for a customer's risk tier."""
    if override_tier and override_tier not in ("safe", "moderate", "risky"):
        raise ValueError(f"Invalid override tier: {override_tier!r}")

    row = db.session.execute(
        db.select(CustomerRiskScore)
        .where(CustomerRiskScore.customer_name == customer_name)
    ).scalar_one_or_none()

    if row is None:
        # Compute first so we have a row to override
        calculate_risk_score(customer_name)
        row = db.session.execute(
            db.select(CustomerRiskScore)
            .where(CustomerRiskScore.customer_name == customer_name)
        ).scalar_one()

    row.override_tier = override_tier
    row.override_by = admin_user_id if override_tier else None
    row.override_at = datetime.now(timezone.utc) if override_tier else None
    db.session.commit()
    return row


def get_risk_summary() -> dict:
    """Quick summary counts for dashboard widget."""
    scores = get_all_customer_risk_scores()
    return {
        "total": len(scores),
        "safe": sum(1 for s in scores if s["risk_level"] == "safe"),
        "watch": sum(1 for s in scores if s["risk_level"] == "moderate"),   # backward compat
        "moderate": sum(1 for s in scores if s["risk_level"] == "moderate"),
        "risky": sum(1 for s in scores if s["risk_level"] == "risky"),
        "total_outstanding": sum(s["total_outstanding"] for s in scores),
    }
