"""Shift management service."""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import func
from ..extensions import db
from ..models.shift import Shift
from ..models.sale import Sale
from ..models.operations import CashSession


def get_open_shift(user_id: int) -> Shift | None:
    return db.session.execute(
        db.select(Shift).where(Shift.user_id == user_id, Shift.status == "open")
    ).scalar_one_or_none()


def open_shift(user_id: int, opening_cash: float, notes: str | None = None) -> Shift:
    if get_open_shift(user_id):
        raise ValueError("You already have an open shift.")
    shift = Shift(
        user_id=user_id,
        opening_cash=Decimal(str(opening_cash)),
        notes=notes,
    )
    db.session.add(shift)
    db.session.commit()
    return shift


def close_shift(shift_id: int, closing_cash: float, notes: str | None = None) -> Shift:
    shift = db.get_or_404(Shift, shift_id)
    if shift.status != "open":
        raise ValueError("This shift is already closed.")

    now = datetime.now(timezone.utc)

    # Calculate sales during this shift
    sales_total = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(Sale.user_id == shift.user_id,
               Sale.sale_date >= shift.started_at,
               Sale.sale_date <= now)
    ).scalar() or 0

    txn_count = db.session.execute(
        db.select(func.count(Sale.id))
        .where(Sale.user_id == shift.user_id,
               Sale.sale_date >= shift.started_at,
               Sale.sale_date <= now)
    ).scalar() or 0

    shift.closing_cash = Decimal(str(closing_cash))
    shift.total_sales = Decimal(str(sales_total))
    shift.total_transactions = int(txn_count)
    shift.ended_at = now
    shift.status = "closed"
    if notes:
        shift.notes = (shift.notes or "") + ("\n" if shift.notes else "") + notes
    db.session.commit()
    return shift


def list_shifts(user_id: int | None = None, limit: int = 50) -> list[Shift]:
    stmt = db.select(Shift).order_by(Shift.started_at.desc()).limit(limit)
    if user_id:
        stmt = stmt.where(Shift.user_id == user_id)
    return db.session.execute(stmt).scalars().all()
