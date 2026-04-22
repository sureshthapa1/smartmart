"""AI Feature 9: Cash Flow Prediction (next 30 days)"""

from __future__ import annotations
from datetime import date, timedelta
from ..extensions import db
from ..models.sale import Sale
from ..models.expense import Expense
from sqlalchemy import func


def predict_cashflow(days_ahead: int = 30) -> dict:
    """Predict cash flow for the next N days using trend + day-of-week weighting."""
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
    dow_series = []  # day of week for each data point
    current = start
    while current <= today:
        rev_series.append(daily_revenue.get(str(current), 0.0))
        exp_series.append(daily_expense.get(str(current), 0.0))
        dow_series.append(current.weekday())  # 0=Mon, 6=Sun
        current += timedelta(days=1)

    avg_rev = sum(rev_series[-30:]) / 30 if rev_series else 0
    avg_exp = sum(exp_series[-30:]) / 30 if exp_series else 0

    # Day-of-week multipliers from actual data
    dow_rev_totals = [0.0] * 7
    dow_rev_counts = [0] * 7
    for i, dow in enumerate(dow_series):
        if rev_series[i] > 0:
            dow_rev_totals[dow] += rev_series[i]
            dow_rev_counts[dow] += 1
    # Compute multiplier: ratio of each day's avg to overall avg
    dow_multipliers = []
    for d in range(7):
        if dow_rev_counts[d] > 0 and avg_rev > 0:
            day_avg = dow_rev_totals[d] / dow_rev_counts[d]
            dow_multipliers.append(day_avg / avg_rev)
        else:
            dow_multipliers.append(1.0)

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
        dow = future.weekday()
        # Trend-adjusted base + day-of-week multiplier
        pred_rev = max(0, (avg_rev + rev_slope * i) * dow_multipliers[dow])
        pred_exp = max(0, avg_exp + exp_slope * i)
        net = pred_rev - pred_exp
        cumulative_cash += net
        forecasts.append({
            "date": str(future),
            "day": future.strftime("%a %d %b"),
            "day_of_week": future.strftime("%A"),
            "predicted_revenue": round(pred_rev, 2),
            "predicted_expense": round(pred_exp, 2),
            "net_cashflow": round(net, 2),
            "cumulative": round(cumulative_cash, 2),
            "dow_multiplier": round(dow_multipliers[dow], 2),
        })

    total_predicted_rev = sum(f["predicted_revenue"] for f in forecasts)
    total_predicted_exp = sum(f["predicted_expense"] for f in forecasts)

    # Find best and worst predicted days
    best_day = max(forecasts, key=lambda x: x["predicted_revenue"]) if forecasts else None
    worst_day = min(forecasts, key=lambda x: x["predicted_revenue"]) if forecasts else None

    return {
        "days_ahead": days_ahead,
        "avg_daily_revenue": round(avg_rev, 2),
        "avg_daily_expense": round(avg_exp, 2),
        "dow_multipliers": {
            ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][d]: round(dow_multipliers[d], 2)
            for d in range(7)
        },
        "forecasts": forecasts,
        "total_predicted_revenue": round(total_predicted_rev, 2),
        "total_predicted_expense": round(total_predicted_exp, 2),
        "total_net_cashflow": round(cumulative_cash, 2),
        "best_day": best_day["day_of_week"] if best_day else None,
        "worst_day": worst_day["day_of_week"] if worst_day else None,
        "outlook": "positive" if cumulative_cash > 0 else "negative",
        "insight": (
            f"Projected NPR {cumulative_cash:,.0f} net cash flow over {days_ahead} days. "
            f"Best day: {best_day['day_of_week'] if best_day else 'N/A'}. "
            f"{'Business looks healthy.' if cumulative_cash > 0 else 'Cash flow risk detected!'}"
        ),
    }
