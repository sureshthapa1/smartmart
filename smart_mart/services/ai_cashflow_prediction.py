"""AI Feature 9: Cash Flow Prediction (next 30 days)"""

from __future__ import annotations
from datetime import date, timedelta
from ..extensions import db
from ..models.sale import Sale
from ..models.expense import Expense
from sqlalchemy import func


def predict_cashflow(days_ahead: int = 30) -> dict:
    """Predict cash flow for the next N days using trend analysis."""
    today = date.today()
    # Use last 60 days as baseline
    start = today - timedelta(days=59)

    daily_revenue = {}
    daily_expense = {}

    rev_rows = db.session.execute(
        db.select(func.date(Sale.sale_date).label("day"), func.sum(Sale.total_amount).label("total"))
        .where(func.date(Sale.sale_date) >= start)
        .group_by(func.date(Sale.sale_date))
    ).all()
    for r in rev_rows:
        daily_revenue[str(r.day)] = float(r.total)

    exp_rows = db.session.execute(
        db.select(Expense.expense_date, func.sum(Expense.amount).label("total"))
        .where(Expense.expense_date >= start)
        .group_by(Expense.expense_date)
    ).all()
    for r in exp_rows:
        daily_expense[str(r.expense_date)] = float(r.total)

    # Build series
    rev_series, exp_series = [], []
    current = start
    while current <= today:
        rev_series.append(daily_revenue.get(str(current), 0.0))
        exp_series.append(daily_expense.get(str(current), 0.0))
        current += timedelta(days=1)

    avg_rev = sum(rev_series[-30:]) / 30 if rev_series else 0
    avg_exp = sum(exp_series[-30:]) / 30 if exp_series else 0

    # Simple trend
    def trend(series):
        n = len(series)
        if n < 2:
            return 0
        x_mean = (n - 1) / 2
        y_mean = sum(series) / n
        num = sum((i - x_mean) * (series[i] - y_mean) for i in range(n))
        den = sum((i - x_mean) ** 2 for i in range(n))
        return num / den if den else 0

    rev_slope = trend(rev_series[-30:])
    exp_slope = trend(exp_series[-30:])

    forecasts = []
    cumulative_cash = 0.0
    for i in range(1, days_ahead + 1):
        future = today + timedelta(days=i)
        pred_rev = max(0, avg_rev + rev_slope * i)
        pred_exp = max(0, avg_exp + exp_slope * i)
        net = pred_rev - pred_exp
        cumulative_cash += net
        forecasts.append({
            "date": str(future),
            "day": future.strftime("%a %d %b"),
            "predicted_revenue": round(pred_rev, 2),
            "predicted_expense": round(pred_exp, 2),
            "net_cashflow": round(net, 2),
            "cumulative": round(cumulative_cash, 2),
        })

    total_predicted_rev = sum(f["predicted_revenue"] for f in forecasts)
    total_predicted_exp = sum(f["predicted_expense"] for f in forecasts)

    return {
        "days_ahead": days_ahead,
        "avg_daily_revenue": round(avg_rev, 2),
        "avg_daily_expense": round(avg_exp, 2),
        "forecasts": forecasts,
        "total_predicted_revenue": round(total_predicted_rev, 2),
        "total_predicted_expense": round(total_predicted_exp, 2),
        "total_net_cashflow": round(cumulative_cash, 2),
        "outlook": "positive" if cumulative_cash > 0 else "negative",
        "insight": (
            f"Projected NPR {cumulative_cash:,.0f} net cash flow over {days_ahead} days. "
            f"{'Business looks healthy.' if cumulative_cash > 0 else 'Cash flow risk detected!'}"
        ),
    }
