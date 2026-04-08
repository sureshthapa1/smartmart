"""End-of-Day summary report service."""
from __future__ import annotations
from datetime import date, datetime, timezone
from sqlalchemy import func
from ..extensions import db
from ..models.sale import Sale, SaleItem
from ..models.expense import Expense
from ..models.product import Product
from ..models.operations import CashSession


def get_eod_summary(target_date: date | None = None) -> dict:
    d = target_date or date.today()

    # Sales breakdown
    sales_rows = db.session.execute(
        db.select(
            Sale.payment_mode,
            func.count(Sale.id).label("count"),
            func.coalesce(func.sum(Sale.total_amount), 0).label("total"),
            func.coalesce(func.sum(Sale.discount_amount), 0).label("discounts"),
        )
        .where(func.date(Sale.sale_date) == d)
        .group_by(Sale.payment_mode)
    ).all()

    payment_breakdown = []
    total_sales = 0
    total_transactions = 0
    total_discounts = 0
    for r in sales_rows:
        payment_breakdown.append({
            "mode": r.payment_mode or "cash",
            "count": r.count,
            "total": float(r.total),
            "discounts": float(r.discounts),
        })
        total_sales += float(r.total)
        total_transactions += r.count
        total_discounts += float(r.discounts)

    # COGS
    cogs = db.session.execute(
        db.select(func.coalesce(func.sum(Product.cost_price * SaleItem.quantity), 0))
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) == d)
    ).scalar() or 0

    # Expenses
    exp_rows = db.session.execute(
        db.select(
            Expense.expense_type,
            func.coalesce(func.sum(Expense.amount), 0).label("total"),
        )
        .where(Expense.expense_date == d)
        .group_by(Expense.expense_type)
    ).all()
    expense_breakdown = [{"type": r.expense_type, "total": float(r.total)} for r in exp_rows]
    total_expenses = sum(r["total"] for r in expense_breakdown)

    gross_profit = total_sales - float(cogs)
    net_profit = gross_profit - total_expenses
    gross_margin = round(gross_profit / total_sales * 100, 1) if total_sales else 0

    # Top 5 products
    top_products = db.session.execute(
        db.select(Product.name, func.sum(SaleItem.quantity).label("qty"),
                  func.sum(SaleItem.subtotal).label("rev"))
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) == d)
        .group_by(Product.id)
        .order_by(func.sum(SaleItem.subtotal).desc())
        .limit(5)
    ).all()

    # Cash session for the day
    sessions = db.session.execute(
        db.select(CashSession)
        .where(func.date(CashSession.opened_at) == d)
        .order_by(CashSession.opened_at)
    ).scalars().all()

    return {
        "date": d.isoformat(),
        "date_display": d.strftime("%A, %d %B %Y"),
        "total_sales": total_sales,
        "total_transactions": total_transactions,
        "total_discounts": total_discounts,
        "cogs": float(cogs),
        "gross_profit": gross_profit,
        "gross_margin": gross_margin,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
        "payment_breakdown": payment_breakdown,
        "expense_breakdown": expense_breakdown,
        "top_products": [{"name": r.name, "qty": int(r.qty), "rev": float(r.rev)} for r in top_products],
        "cash_sessions": sessions,
        "avg_order_value": round(total_sales / total_transactions, 2) if total_transactions else 0,
    }
