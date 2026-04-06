"""API blueprint — JSON endpoints for Chart.js charts."""

from datetime import date, timedelta

from flask import Blueprint, jsonify
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
