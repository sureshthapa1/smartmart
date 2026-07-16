"""Cash flow management service — income, expenses, daily balance, and profit/loss."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, func

from ..extensions import db
from ..models.expense import Expense
from ..models.sale import Sale, SaleItem
from ..models.product import Product


def record_income(sale) -> None:
    """Record a sale's total as an income entry (no-op — income is derived from Sale records)."""
    # Income is read directly from the Sale table; no separate income record needed.
    pass


def record_expense(expense_type: str, amount, expense_date: date, user_id: int, note: str = None) -> Expense:
    """Create and persist an Expense record."""
    expense = Expense(
        expense_type=expense_type,
        amount=amount,
        expense_date=expense_date,
        note=note,
        created_by=user_id,
    )
    db.session.add(expense)
    db.session.commit()
    return expense


def daily_balance(target_date: date) -> Decimal:
    """Return total income minus total expenses for a given date."""
    from datetime import timedelta
    day_start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc)
    day_end   = day_start + timedelta(days=1)

    income = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0)).where(
            Sale.sale_date >= day_start,
            Sale.sale_date < day_end,
        )
    ).scalar() or Decimal("0")

    expenses = db.session.execute(
        db.select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.expense_date == target_date
        )
    ).scalar() or Decimal("0")

    return Decimal(str(income)) - Decimal(str(expenses))


def profit_loss(start: date, end: date) -> dict:
    """Calculate profit/loss for a date range.

    Returns a dict with keys: revenue, cogs, other_expenses, profit.
    profit = revenue - cogs - other_expenses
    """
    # Total sales revenue
    revenue = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0)).where(
            and_(Sale.sale_date >= start, Sale.sale_date <= end)
        )
    ).scalar() or Decimal("0")

    # Cost of goods sold — use historical cost_price stored on SaleItem (snapshot at time
    # of sale) so figures remain accurate even after product cost changes.
    cogs_row = db.session.execute(
        db.select(
            func.coalesce(
                func.sum(func.coalesce(SaleItem.cost_price, Product.cost_price) * SaleItem.quantity), 0
            )
        )
        .select_from(SaleItem)
        .join(Product, Product.id == SaleItem.product_id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(and_(Sale.sale_date >= start, Sale.sale_date <= end))
    ).scalar()
    cogs = Decimal(str(cogs_row or 0))

    # Non-purchase expenses
    other_expenses = db.session.execute(
        db.select(func.coalesce(func.sum(Expense.amount), 0)).where(
            and_(
                Expense.expense_date >= start,
                Expense.expense_date <= end,
                Expense.expense_type != "purchase",
            )
        )
    ).scalar() or Decimal("0")

    revenue = Decimal(str(revenue))
    cogs = Decimal(str(cogs))
    other_expenses = Decimal(str(other_expenses))

    return {
        "revenue": revenue,
        "cogs": cogs,
        "other_expenses": other_expenses,
        "profit": revenue - cogs - other_expenses,
    }
