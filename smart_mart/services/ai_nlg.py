"""AI Feature 5: Natural Language Report Generation

Converts business data into plain English summaries.
No external NLP library needed — uses template-based generation
with dynamic data insertion and conditional logic.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func

from ..extensions import db
from ..models.product import Product
from ..models.sale import Sale, SaleItem
from ..models.expense import Expense


def _pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return ((current - previous) / previous) * 100


def _trend_word(pct: float) -> str:
    if pct >= 20:
        return "surged significantly"
    elif pct >= 10:
        return "increased notably"
    elif pct >= 3:
        return "grew slightly"
    elif pct >= -3:
        return "remained stable"
    elif pct >= -10:
        return "declined slightly"
    elif pct >= -20:
        return "dropped notably"
    else:
        return "fell sharply"


def generate_daily_report() -> dict:
    """Generate a plain English daily business summary."""
    today = date.today()
    yesterday = today - timedelta(days=1)
    week_start = today - timedelta(days=today.weekday())

    # Today's data
    today_sales = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) == today)
    ).scalar() or 0
    today_count = db.session.execute(
        db.select(func.count(Sale.id))
        .where(func.date(Sale.sale_date) == today)
    ).scalar() or 0

    # Yesterday's data
    yesterday_sales = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) == yesterday)
    ).scalar() or 0

    # Top product today
    top_today = db.session.execute(
        db.select(Product, func.sum(SaleItem.quantity).label("qty"))
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) == today)
        .group_by(Product.id)
        .order_by(func.sum(SaleItem.quantity).desc())
        .limit(1)
    ).first()

    # Low stock count
    low_stock_count = db.session.execute(
        db.select(func.count(Product.id)).where(Product.quantity <= 10)
    ).scalar() or 0

    # COGS today
    cogs_rows = db.session.execute(
        db.select(Product.cost_price, func.sum(SaleItem.quantity).label("qty"))
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) == today)
        .group_by(Product.id)
    ).all()
    today_cogs = sum(float(r.cost_price) * r.qty for r in cogs_rows)
    today_profit = float(today_sales) - today_cogs

    pct = _pct_change(float(today_sales), float(yesterday_sales))
    trend = _trend_word(pct)

    # Build narrative
    paragraphs = []

    # Opening
    if float(today_sales) == 0:
        paragraphs.append(f"📅 **{today.strftime('%A, %B %d %Y')}** — No sales recorded today yet.")
    else:
        paragraphs.append(
            f"📅 **{today.strftime('%A, %B %d %Y')}** — Today's business {trend} "
            f"with **NPR {float(today_sales):,.0f}** in revenue from **{today_count} transaction(s)**."
        )

    # Comparison
    if float(yesterday_sales) > 0:
        direction = "up" if pct >= 0 else "down"
        paragraphs.append(
            f"Compared to yesterday (NPR {float(yesterday_sales):,.0f}), "
            f"sales are **{direction} {abs(pct):.1f}%**."
        )

    # Profit
    if today_profit > 0:
        paragraphs.append(f"💰 Estimated profit today: **NPR {today_profit:,.0f}**.")
    elif today_profit < 0:
        paragraphs.append(f"⚠️ Today is running at a loss of NPR {abs(today_profit):,.0f}.")

    # Top product
    if top_today:
        paragraphs.append(
            f"🏆 Best-selling product today: **{top_today.Product.name}** ({top_today.qty} units sold)."
        )

    # Alerts
    if low_stock_count > 0:
        paragraphs.append(
            f"⚠️ **{low_stock_count} product(s)** are running low on stock. Consider restocking."
        )

    return {
        "type": "daily",
        "date": str(today),
        "title": f"Daily Business Report — {today.strftime('%B %d, %Y')}",
        "narrative": "\n\n".join(paragraphs),
        "paragraphs": paragraphs,
        "data": {
            "today_sales": float(today_sales),
            "today_count": today_count,
            "today_profit": round(today_profit, 2),
            "vs_yesterday_pct": round(pct, 1),
            "low_stock_count": low_stock_count,
        },
    }


def generate_weekly_report() -> dict:
    """Generate a plain English weekly business summary."""
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    last_week_start = week_start - timedelta(days=7)
    last_week_end = week_start - timedelta(days=1)

    this_week_sales = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= week_start)
    ).scalar() or 0
    this_week_count = db.session.execute(
        db.select(func.count(Sale.id))
        .where(func.date(Sale.sale_date) >= week_start)
    ).scalar() or 0

    last_week_sales = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= last_week_start)
        .where(func.date(Sale.sale_date) <= last_week_end)
    ).scalar() or 0

    # Top 3 products this week
    top3 = db.session.execute(
        db.select(Product, func.sum(SaleItem.quantity).label("qty"))
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= week_start)
        .group_by(Product.id)
        .order_by(func.sum(SaleItem.quantity).desc())
        .limit(3)
    ).all()

    pct = _pct_change(float(this_week_sales), float(last_week_sales))
    trend = _trend_word(pct)

    paragraphs = []
    paragraphs.append(
        f"📊 **Week of {week_start.strftime('%B %d')}** — This week's sales {trend} "
        f"at **NPR {float(this_week_sales):,.0f}** from {this_week_count} transactions."
    )

    if float(last_week_sales) > 0:
        direction = "up" if pct >= 0 else "down"
        paragraphs.append(
            f"Week-over-week performance is **{direction} {abs(pct):.1f}%** "
            f"(last week: NPR {float(last_week_sales):,.0f})."
        )

    if top3:
        names = ", ".join(f"**{r.Product.name}** ({r.qty} units)" for r in top3)
        paragraphs.append(f"🏆 Top products this week: {names}.")

    avg_daily = float(this_week_sales) / max((today - week_start).days + 1, 1)
    paragraphs.append(f"📈 Average daily revenue this week: **NPR {avg_daily:,.0f}**.")

    return {
        "type": "weekly",
        "week_start": str(week_start),
        "title": f"Weekly Business Report — Week of {week_start.strftime('%B %d, %Y')}",
        "narrative": "\n\n".join(paragraphs),
        "paragraphs": paragraphs,
        "data": {
            "this_week_sales": float(this_week_sales),
            "this_week_count": this_week_count,
            "last_week_sales": float(last_week_sales),
            "vs_last_week_pct": round(pct, 1),
            "avg_daily_revenue": round(avg_daily, 2),
        },
    }


def generate_monthly_report() -> dict:
    """Generate a plain English monthly business summary."""
    today = date.today()
    month_start = today.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    this_month_sales = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= month_start)
    ).scalar() or 0
    last_month_sales = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= last_month_start)
        .where(func.date(Sale.sale_date) <= last_month_end)
    ).scalar() or 0

    # Expenses this month
    expenses = db.session.execute(
        db.select(func.coalesce(func.sum(Expense.amount), 0))
        .where(Expense.expense_date >= month_start)
    ).scalar() or 0

    # COGS
    cogs_rows = db.session.execute(
        db.select(Product.cost_price, func.sum(SaleItem.quantity).label("qty"))
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= month_start)
        .group_by(Product.id)
    ).all()
    cogs = sum(float(r.cost_price) * r.qty for r in cogs_rows)
    net_profit = float(this_month_sales) - cogs - float(expenses)

    pct = _pct_change(float(this_month_sales), float(last_month_sales))
    trend = _trend_word(pct)

    paragraphs = []
    paragraphs.append(
        f"📆 **{today.strftime('%B %Y')} Business Summary** — Monthly revenue {trend} "
        f"at **NPR {float(this_month_sales):,.0f}**."
    )

    if float(last_month_sales) > 0:
        direction = "up" if pct >= 0 else "down"
        paragraphs.append(
            f"Month-over-month: **{direction} {abs(pct):.1f}%** "
            f"(last month: NPR {float(last_month_sales):,.0f})."
        )

    if net_profit > 0:
        margin = (net_profit / float(this_month_sales) * 100) if float(this_month_sales) > 0 else 0
        paragraphs.append(
            f"💰 Net profit this month: **NPR {net_profit:,.0f}** "
            f"({margin:.1f}% margin) after NPR {cogs:,.0f} in COGS and NPR {float(expenses):,.0f} in expenses."
        )
    elif net_profit < 0:
        paragraphs.append(f"⚠️ Net loss this month: **NPR {abs(net_profit):,.0f}**. Review expenses and pricing.")

    return {
        "type": "monthly",
        "month": today.strftime("%B %Y"),
        "title": f"Monthly Business Report — {today.strftime('%B %Y')}",
        "narrative": "\n\n".join(paragraphs),
        "paragraphs": paragraphs,
        "data": {
            "this_month_sales": float(this_month_sales),
            "last_month_sales": float(last_month_sales),
            "net_profit": round(net_profit, 2),
            "expenses": float(expenses),
            "cogs": round(cogs, 2),
            "vs_last_month_pct": round(pct, 1),
        },
    }
