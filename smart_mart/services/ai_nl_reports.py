"""AI Feature 5: Natural Language Report Generation

Converts business data into plain English summaries.
"""

from __future__ import annotations
from datetime import date, timedelta
from sqlalchemy import func
from ..extensions import db
from ..models.sale import Sale, SaleItem
from ..models.product import Product
from ..models.expense import Expense


def generate_weekly_summary() -> str:
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    prev_week_start = week_start - timedelta(days=7)
    prev_week_end = week_start - timedelta(days=1)

    this_rev = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= week_start)
    ).scalar() or 0

    prev_rev = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= prev_week_start)
        .where(func.date(Sale.sale_date) <= prev_week_end)
    ).scalar() or 0

    txn_count = db.session.execute(
        db.select(func.count(Sale.id))
        .where(func.date(Sale.sale_date) >= week_start)
    ).scalar() or 0

    top = db.session.execute(
        db.select(Product, func.sum(SaleItem.quantity).label("qty"))
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= week_start)
        .group_by(Product.id)
        .order_by(func.sum(SaleItem.quantity).desc())
        .limit(1)
    ).first()

    low_stock = db.session.execute(
        db.select(func.count(Product.id)).where(Product.quantity <= 10)
    ).scalar() or 0

    # Build natural language
    parts = [f"📊 **Weekly Business Summary — Week of {week_start}**\n"]

    if float(prev_rev) > 0:
        change = ((float(this_rev) - float(prev_rev)) / float(prev_rev)) * 100
        if change > 5:
            parts.append(f"✅ Sales are up {change:.1f}% this week at NPR {float(this_rev):,.0f} from {txn_count} transactions.")
        elif change < -5:
            parts.append(f"⚠️ Sales dropped {abs(change):.1f}% this week to NPR {float(this_rev):,.0f}. Last week was NPR {float(prev_rev):,.0f}.")
        else:
            parts.append(f"➡️ Sales are stable at NPR {float(this_rev):,.0f} this week ({txn_count} transactions).")
    else:
        parts.append(f"📈 This week's sales: NPR {float(this_rev):,.0f} from {txn_count} transactions.")

    if top:
        parts.append(f"🏆 Top selling product: **{top.Product.name}** with {top.qty} units sold.")

    if low_stock > 0:
        parts.append(f"⚠️ {low_stock} product(s) are running low on stock and need restocking.")

    return "\n".join(parts)


def generate_monthly_summary() -> str:
    today = date.today()
    month_start = today.replace(day=1)
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)

    this_rev = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= month_start)
    ).scalar() or 0

    prev_rev = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= prev_month_start)
        .where(func.date(Sale.sale_date) <= prev_month_end)
    ).scalar() or 0

    expenses = db.session.execute(
        db.select(func.coalesce(func.sum(Expense.amount), 0))
        .where(Expense.expense_date >= month_start)
    ).scalar() or 0

    cogs_rows = db.session.execute(
        db.select(Product.cost_price, func.sum(SaleItem.quantity).label("qty"))
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= month_start)
        .group_by(Product.id)
    ).all()
    cogs = sum(float(r.cost_price) * r.qty for r in cogs_rows)
    profit = float(this_rev) - cogs - float(expenses)

    parts = [f"📅 **Monthly Business Report — {today.strftime('%B %Y')}**\n"]
    parts.append(f"💰 Total Revenue: NPR {float(this_rev):,.0f}")
    parts.append(f"📦 Cost of Goods: NPR {cogs:,.0f}")
    parts.append(f"💸 Expenses: NPR {float(expenses):,.0f}")
    parts.append(f"{'✅' if profit > 0 else '❌'} Net Profit: NPR {profit:,.0f}")

    if float(prev_rev) > 0:
        change = ((float(this_rev) - float(prev_rev)) / float(prev_rev)) * 100
        parts.append(f"\n{'📈' if change > 0 else '📉'} Revenue {'increased' if change > 0 else 'decreased'} {abs(change):.1f}% vs last month (NPR {float(prev_rev):,.0f}).")

    return "\n".join(parts)


def generate_daily_briefing() -> str:
    today = date.today()
    rev = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) == today)
    ).scalar() or 0
    txns = db.session.execute(
        db.select(func.count(Sale.id))
        .where(func.date(Sale.sale_date) == today)
    ).scalar() or 0
    out_of_stock = db.session.execute(
        db.select(func.count(Product.id)).where(Product.quantity == 0)
    ).scalar() or 0

    parts = [f"☀️ **Daily Briefing — {today.strftime('%A, %d %B %Y')}**\n"]
    if float(rev) > 0:
        parts.append(f"💵 Today's sales: NPR {float(rev):,.0f} from {txns} transaction(s).")
    else:
        parts.append("📭 No sales recorded today yet.")
    if out_of_stock > 0:
        parts.append(f"🚨 {out_of_stock} product(s) are out of stock.")
    parts.append("✅ System is running normally.")
    return "\n".join(parts)
