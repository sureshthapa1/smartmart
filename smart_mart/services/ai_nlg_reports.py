"""AI Feature 5: Natural Language Report Generation

Converts business data into plain English summaries.
No external NLP library needed — template-based generation with data interpolation.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func

from ..extensions import db
from ..models.product import Product
from ..models.sale import Sale, SaleItem
from ..models.expense import Expense
from ..services import cash_flow_manager


def _pct_change(current: float, previous: float) -> tuple[float, str]:
    if previous == 0:
        return 0.0, "unchanged"
    pct = ((current - previous) / previous) * 100
    direction = "increased" if pct > 0 else "decreased"
    return round(abs(pct), 1), direction


def generate_daily_report() -> dict:
    """Generate a plain English daily business summary."""
    today = date.today()
    yesterday = today - timedelta(days=1)

    today_sales = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) == today)
    ).scalar() or 0

    yesterday_sales = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) == yesterday)
    ).scalar() or 0

    today_count = db.session.execute(
        db.select(func.count(Sale.id))
        .where(func.date(Sale.sale_date) == today)
    ).scalar() or 0

    # Top product today
    top = db.session.execute(
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

    pct, direction = _pct_change(float(today_sales), float(yesterday_sales))

    # Build narrative
    sentences = []
    sentences.append(f"Today is {today.strftime('%A, %B %d, %Y')}.")

    if today_count == 0:
        sentences.append("No sales have been recorded today yet.")
    else:
        sentences.append(
            f"So far today, {today_count} transaction{'s' if today_count > 1 else ''} "
            f"generated NPR {float(today_sales):,.0f} in revenue."
        )

    if float(yesterday_sales) > 0:
        if pct > 0:
            sentences.append(
                f"This is {pct}% {direction} compared to yesterday's NPR {float(yesterday_sales):,.0f}."
            )
        else:
            sentences.append(f"Sales are on par with yesterday.")

    if top:
        sentences.append(
            f"The best-selling product today is {top.Product.name} with {top.qty} units sold."
        )

    if low_stock_count > 0:
        sentences.append(
            f"⚠️ {low_stock_count} product{'s' if low_stock_count > 1 else ''} "
            f"{'are' if low_stock_count > 1 else 'is'} running low on stock and may need restocking."
        )

    return {
        "type": "daily",
        "date": str(today),
        "narrative": " ".join(sentences),
        "data": {
            "today_sales": float(today_sales),
            "today_count": today_count,
            "yesterday_sales": float(yesterday_sales),
            "change_pct": pct,
            "change_direction": direction,
            "top_product": top.Product.name if top else None,
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

    last_week_sales = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= last_week_start)
        .where(func.date(Sale.sale_date) <= last_week_end)
    ).scalar() or 0

    this_week_count = db.session.execute(
        db.select(func.count(Sale.id))
        .where(func.date(Sale.sale_date) >= week_start)
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

    # Profit this week
    pl = cash_flow_manager.profit_loss(week_start, today)
    pct, direction = _pct_change(float(this_week_sales), float(last_week_sales))

    sentences = []
    sentences.append(f"Weekly Business Summary — Week of {week_start.strftime('%B %d, %Y')}.")
    sentences.append(
        f"This week, {this_week_count} sales generated NPR {float(this_week_sales):,.0f} in revenue."
    )

    if float(last_week_sales) > 0:
        sentences.append(
            f"Revenue has {direction} by {pct}% compared to last week "
            f"(NPR {float(last_week_sales):,.0f})."
        )

    if top3:
        names = ", ".join(r.Product.name for r in top3)
        sentences.append(f"Top selling products this week: {names}.")

    if float(pl["profit"]) > 0:
        sentences.append(f"Net profit for the week stands at NPR {float(pl['profit']):,.0f}.")
    elif float(pl["profit"]) < 0:
        sentences.append(
            f"⚠️ The business recorded a net loss of NPR {abs(float(pl['profit'])):,.0f} this week."
        )

    return {
        "type": "weekly",
        "week_start": str(week_start),
        "narrative": " ".join(sentences),
        "data": {
            "this_week_sales": float(this_week_sales),
            "last_week_sales": float(last_week_sales),
            "change_pct": pct,
            "change_direction": direction,
            "transactions": this_week_count,
            "net_profit": float(pl["profit"]),
            "top_products": [r.Product.name for r in top3],
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

    pl = cash_flow_manager.profit_loss(month_start, today)
    pct, direction = _pct_change(float(this_month_sales), float(last_month_sales))

    # Dead stock
    cutoff = today - timedelta(days=30)
    sold_ids = db.session.execute(
        db.select(SaleItem.product_id.distinct())
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= cutoff)
    ).scalars().all()
    dead_count = db.session.execute(
        db.select(func.count(Product.id))
        .where(Product.id.notin_(sold_ids) if sold_ids else db.true())
        .where(Product.quantity > 0)
    ).scalar() or 0

    sentences = []
    sentences.append(f"Monthly Business Report — {today.strftime('%B %Y')}.")
    sentences.append(
        f"Revenue this month: NPR {float(this_month_sales):,.0f}."
    )
    if float(last_month_sales) > 0:
        sentences.append(
            f"Compared to last month (NPR {float(last_month_sales):,.0f}), "
            f"revenue has {direction} by {pct}%."
        )
    if float(pl["profit"]) > 0:
        sentences.append(f"Net profit: NPR {float(pl['profit']):,.0f}.")
    else:
        sentences.append(f"Net loss: NPR {abs(float(pl['profit'])):,.0f}. Review expenses.")
    if dead_count > 0:
        sentences.append(
            f"{dead_count} product{'s' if dead_count > 1 else ''} had no sales this month — "
            f"consider clearance pricing."
        )

    return {
        "type": "monthly",
        "month": today.strftime("%B %Y"),
        "narrative": " ".join(sentences),
        "data": {
            "this_month_sales": float(this_month_sales),
            "last_month_sales": float(last_month_sales),
            "change_pct": pct,
            "change_direction": direction,
            "net_profit": float(pl["profit"]),
            "dead_stock_count": dead_count,
        },
    }
