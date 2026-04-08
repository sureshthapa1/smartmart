"""AI Feature 3: Transaction Anomaly Detection

Detects:
- Unusual sales spikes/drops
- Suspicious discount patterns by staff
- Same product sold at very different prices
- Off-hours transactions
"""

from __future__ import annotations
from datetime import date, timedelta
from statistics import mean, stdev
from sqlalchemy import func
from ..extensions import db
from ..models.sale import Sale, SaleItem
from ..models.product import Product


def detect_price_anomalies(days: int = 30) -> list[dict]:
    """Detect products sold at significantly different prices."""
    start = date.today() - timedelta(days=days)
    rows = db.session.execute(
        db.select(
            Product,
            func.min(SaleItem.unit_price).label("min_price"),
            func.max(SaleItem.unit_price).label("max_price"),
            func.avg(SaleItem.unit_price).label("avg_price"),
            func.count(SaleItem.id).label("txn_count"),
        )
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= start)
        .group_by(Product.id)
        .having(func.count(SaleItem.id) >= 2)
    ).all()

    anomalies = []
    for r in rows:
        min_p, max_p, avg_p = float(r.min_price), float(r.max_price), float(r.avg_price)
        if avg_p > 0:
            variance_pct = ((max_p - min_p) / avg_p) * 100
            if variance_pct > 25:
                anomalies.append({
                    "product_id": r.Product.id,
                    "product_name": r.Product.name,
                    "min_price": min_p,
                    "max_price": max_p,
                    "avg_price": round(avg_p, 2),
                    "variance_pct": round(variance_pct, 1),
                    "transaction_count": r.txn_count,
                    "severity": "high" if variance_pct > 50 else "medium",
                    "message": f"Price varied {variance_pct:.1f}% (NPR {min_p:.0f}–{max_p:.0f})",
                })

    anomalies.sort(key=lambda x: x["variance_pct"], reverse=True)
    return anomalies


def detect_sales_spikes(days: int = 30) -> list[dict]:
    """Detect unusual daily sales spikes or drops."""
    end = date.today()
    start = end - timedelta(days=days)

    rows = db.session.execute(
        db.select(
            func.date(Sale.sale_date).label("day"),
            func.sum(Sale.total_amount).label("total"),
            func.count(Sale.id).label("count"),
        )
        .where(func.date(Sale.sale_date) >= start)
        .group_by(func.date(Sale.sale_date))
        .order_by(func.date(Sale.sale_date))
    ).all()

    if len(rows) < 5:
        return []

    daily_totals = [float(r.total) for r in rows]
    avg = mean(daily_totals)
    sd = stdev(daily_totals) if len(daily_totals) > 1 else 0

    anomalies = []
    for r in rows:
        total = float(r.total)
        if sd > 0:
            z_score = (total - avg) / sd
            if abs(z_score) > 2:
                anomalies.append({
                    "date": str(r.day),
                    "total": total,
                    "transactions": r.count,
                    "z_score": round(z_score, 2),
                    "type": "spike" if z_score > 0 else "drop",
                    "severity": "high" if abs(z_score) > 3 else "medium",
                    "message": f"{'Unusual spike' if z_score > 0 else 'Unusual drop'}: NPR {total:,.0f} (avg NPR {avg:,.0f})",
                })

    return anomalies


def detect_suspicious_discounts(days: int = 30) -> list[dict]:
    """Detect staff giving unusually high discounts."""
    start = date.today() - timedelta(days=days)
    sales_with_discount = db.session.execute(
        db.select(Sale)
        .where(func.date(Sale.sale_date) >= start)
        .where(Sale.discount_amount > 0)
    ).scalars().all()

    staff_data = {}
    for s in sales_with_discount:
        uid = s.user_id
        if uid not in staff_data:
            staff_data[uid] = {
                "username": s.user.username if s.user else "Unknown",
                "discounts": [],
                "total_discount": 0,
            }
        disc_pct = (float(s.discount_amount) / (float(s.total_amount) + float(s.discount_amount))) * 100
        staff_data[uid]["discounts"].append(disc_pct)
        staff_data[uid]["total_discount"] += float(s.discount_amount)

    suspicious = []
    for uid, data in staff_data.items():
        avg_disc = mean(data["discounts"])
        if avg_disc > 15:
            suspicious.append({
                "user_id": uid,
                "username": data["username"],
                "avg_discount_pct": round(avg_disc, 1),
                "total_discount_given": round(data["total_discount"], 2),
                "discount_count": len(data["discounts"]),
                "severity": "high" if avg_disc > 25 else "medium",
                "message": f"Avg discount {avg_disc:.1f}% — above normal threshold",
            })

    suspicious.sort(key=lambda x: x["avg_discount_pct"], reverse=True)
    return suspicious


def detect_off_hours_transactions(start_hour: int = 8, end_hour: int = 20) -> list[dict]:
    """Detect transactions outside normal business hours."""
    rows = db.session.execute(
        db.select(Sale)
        .where(
            db.or_(
                func.strftime('%H', Sale.sale_date).cast(db.Integer) < start_hour,
                func.strftime('%H', Sale.sale_date).cast(db.Integer) >= end_hour,
            )
        )
        .order_by(Sale.sale_date.desc())
        .limit(20)
    ).scalars().all()

    return [{
        "sale_id": s.id,
        "invoice": s.invoice_number or f"#{s.id}",
        "time": s.sale_date.strftime("%Y-%m-%d %H:%M") if s.sale_date else "",
        "amount": float(s.total_amount),
        "staff": s.user.username if s.user else "Unknown",
        "message": f"Transaction at {s.sale_date.strftime('%H:%M') if s.sale_date else '?'} — outside business hours",
    } for s in rows]


def full_anomaly_report(days: int = 30) -> dict:
    """Complete anomaly detection report."""
    price_anomalies = detect_price_anomalies(days)
    spikes = detect_sales_spikes(days)
    discounts = detect_suspicious_discounts(days)
    off_hours = detect_off_hours_transactions()

    total_issues = len(price_anomalies) + len(spikes) + len(discounts) + len(off_hours)
    risk_level = "high" if total_issues > 10 else "medium" if total_issues > 3 else "low"

    return {
        "risk_level": risk_level,
        "total_anomalies": total_issues,
        "price_anomalies": price_anomalies,
        "sales_spikes": spikes,
        "suspicious_discounts": discounts,
        "off_hours_transactions": off_hours,
        "summary": f"{total_issues} anomalies detected. Risk level: {risk_level.upper()}.",
        "generated_at": str(date.today()),
    }
