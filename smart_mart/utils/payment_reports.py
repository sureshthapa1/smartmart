from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func

from ..extensions import db
from ..models.sale import PAYMENT_METHODS, Sale


PAYMENT_LABELS = dict(PAYMENT_METHODS)
NEPAL_TZ = timezone(timedelta(hours=5, minutes=45))


def _nepal_day_bounds(target_date):
    start_local = datetime.combine(target_date, time.min, tzinfo=NEPAL_TZ)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def daily_payment_reconciliation(target_date=None):
    target_date = target_date or datetime.now(NEPAL_TZ).date()
    start_utc, end_utc = _nepal_day_bounds(target_date)
    rows = db.session.execute(
        db.select(
            func.coalesce(Sale.payment_method, Sale.payment_mode, "cash").label("method"),
            func.count(Sale.id).label("sale_count"),
            func.coalesce(func.sum(Sale.total_amount), 0).label("total_collected"),
        )
        .where(Sale.sale_date >= start_utc, Sale.sale_date < end_utc)
        .group_by(func.coalesce(Sale.payment_method, Sale.payment_mode, "cash"))
    ).all()

    by_method = {
        method: {"method": method, "label": label, "sale_count": 0, "total_collected": 0.0}
        for method, label in PAYMENT_METHODS
    }
    for row in rows:
        method = row.method or "cash"
        if method not in by_method:
            by_method[method] = {"method": method, "label": method.title(), "sale_count": 0, "total_collected": 0.0}
        by_method[method]["sale_count"] = int(row.sale_count or 0)
        by_method[method]["total_collected"] = float(row.total_collected or 0)

    records = list(by_method.values())
    cash_total = by_method.get("cash", {}).get("total_collected", 0.0)
    digital_total = sum(r["total_collected"] for r in records if r["method"] not in ("cash", "credit"))
    credit_total = by_method.get("credit", {}).get("total_collected", 0.0)

    return {
        "date": target_date,
        "records": records,
        "cash_total": cash_total,
        "digital_total": digital_total,
        "credit_total": credit_total,
        "grand_total": sum(r["total_collected"] for r in records),
    }
