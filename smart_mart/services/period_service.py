"""Financial period close/lock service + accounting export."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func

from ..extensions import db
from ..models.financial_period import FinancialPeriod
from ..models.sale import Sale, SaleItem
from ..models.product import Product
from ..models.expense import Expense


def _money(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))


def list_periods(limit: int = 24) -> list[FinancialPeriod]:
    return db.session.execute(
        db.select(FinancialPeriod)
        .order_by(FinancialPeriod.year.desc(), FinancialPeriod.month.desc())
        .limit(limit)
    ).scalars().all()


def close_period(year: int, month: int, user_id: int, notes: str | None = None) -> FinancialPeriod:
    """Snapshot P&L and mark period closed."""
    period = FinancialPeriod.get_or_create(year, month)
    if period.status in ("closed", "locked"):
        raise ValueError(f"Period {period.label} is already {period.status}.")

    start = date(year, month, 1)
    # Last day of month
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    # end is exclusive — use < end in queries

    total_sales = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) < end)
    ).scalar() or 0

    total_cogs = db.session.execute(
        db.select(func.coalesce(func.sum(SaleItem.quantity * SaleItem.cost_price), 0))
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) < end)
    ).scalar() or 0

    total_opex = db.session.execute(
        db.select(func.coalesce(func.sum(Expense.amount), 0))
        .where(Expense.expense_date >= start, Expense.expense_date < end)
    ).scalar() or 0

    net_profit = _money(total_sales) - _money(total_cogs) - _money(total_opex)

    period.status = "closed"
    period.closed_by = user_id
    period.closed_at = datetime.now(timezone.utc)
    period.notes = notes
    period.total_sales = _money(total_sales)
    period.total_cogs = _money(total_cogs)
    period.total_opex = _money(total_opex)
    period.net_profit = net_profit
    db.session.commit()
    return period


def reopen_period(period_id: int, user_id: int) -> FinancialPeriod:
    period = db.get_or_404(FinancialPeriod, period_id)
    if period.status == "locked":
        raise ValueError("Locked periods cannot be reopened.")
    period.status = "open"
    period.closed_by = None
    period.closed_at = None
    db.session.commit()
    return period


def lock_period(period_id: int) -> FinancialPeriod:
    """Permanently lock — no further edits allowed."""
    period = db.get_or_404(FinancialPeriod, period_id)
    if period.status != "closed":
        raise ValueError("Only closed periods can be locked.")
    period.status = "locked"
    db.session.commit()
    return period


# ── Accounting export ─────────────────────────────────────────────────────────

def export_period_csv(period: FinancialPeriod) -> str:
    """Export a closed period's P&L summary as CSV."""
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["Smart Mart — Accounting Export"])
    w.writerow(["Period", period.label])
    w.writerow(["Status", period.status])
    w.writerow(["Closed At", period.closed_at.isoformat() if period.closed_at else ""])
    w.writerow([])
    w.writerow(["Metric", "Amount (NPR)"])
    w.writerow(["Total Sales", str(period.total_sales or 0)])
    w.writerow(["Total COGS", str(period.total_cogs or 0)])
    w.writerow(["Gross Profit", str(_money(period.total_sales or 0) - _money(period.total_cogs or 0))])
    w.writerow(["Total OpEx", str(period.total_opex or 0)])
    w.writerow(["Net Profit", str(period.net_profit or 0)])
    return output.getvalue()


def export_period_sales_csv(year: int, month: int) -> str:
    """Detailed sales lines for a period — suitable for accounting import."""
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

    rows = db.session.execute(
        db.select(
            Sale.invoice_number,
            func.date(Sale.sale_date).label("date"),
            Sale.customer_name,
            Sale.payment_mode,
            Sale.total_amount,
            Sale.discount_amount,
            Sale.tax_amount,
        )
        .where(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) < end)
        .order_by(Sale.sale_date)
    ).all()

    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["Invoice", "Date", "Customer", "Payment Mode",
                "Total (NPR)", "Discount (NPR)", "Tax (NPR)"])
    for r in rows:
        w.writerow([
            r.invoice_number or "", r.date, r.customer_name or "Walk-in",
            r.payment_mode, r.total_amount, r.discount_amount or 0, r.tax_amount or 0,
        ])
    return output.getvalue()
