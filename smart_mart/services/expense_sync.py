"""expense_sync — keeps bi_operating_expenses in sync with expenses.

Every Expense record has a mirror row in bi_operating_expenses so the BI
P&L, break-even, and profit-tracking reports automatically include all
expenses entered through the regular Expense Management UI.

The link is tracked via a new column  bi_opex_id  on the expenses table
(added by schema migration).  If that column is missing the sync silently
skips rather than crashing.
"""
from __future__ import annotations

import logging
from datetime import date

from ..extensions import db
from ..models.expense import Expense

logger = logging.getLogger(__name__)

# Map expense_type → BI category label
_TYPE_TO_CATEGORY: dict[str, str] = {
    "rent":          "rent",
    "salary":        "salary",
    "utilities":     "utilities",
    "purchase":      "purchase",
    "miscellaneous": "miscellaneous",
    "other":         "other",
}


def _get_opex_model():
    from ..bi.models.operating_expense import OperatingExpense
    return OperatingExpense


def sync_create(expense: Expense) -> None:
    """Create a matching bi_operating_expenses row and store its id back.
    
    Purchase-type expenses are excluded — their cost is already captured
    in COGS via SaleItem.cost_price. Including them in OPEX would double-count.
    """
    try:
        # Purchase costs are already in COGS — don't double-count in OPEX
        if expense.expense_type == "purchase":
            return
        OperatingExpense = _get_opex_model()
        opex = OperatingExpense(
            category=_TYPE_TO_CATEGORY.get(expense.expense_type, expense.expense_type),
            amount=expense.amount,
            expense_date=expense.expense_date,
            payment_method="cash",          # default; BI OPEX can be refined later
            note=expense.note,
        )
        db.session.add(opex)
        db.session.flush()                  # get opex.id before commit
        # Store back-reference if column exists
        try:
            expense.bi_opex_id = opex.id
        except AttributeError:
            pass
        logger.debug("expense_sync: created bi_opex id=%s for expense id=%s", opex.id, expense.id)
    except Exception as exc:
        logger.warning("expense_sync.sync_create failed (non-fatal): %s", exc)


def sync_update(expense: Expense) -> None:
    """Update the mirror row when an Expense is edited."""
    try:
        bi_opex_id = getattr(expense, "bi_opex_id", None)
        if not bi_opex_id:
            # No mirror yet — create one now
            sync_create(expense)
            return
        OperatingExpense = _get_opex_model()
        opex = db.session.get(OperatingExpense, bi_opex_id)
        if opex is None:
            sync_create(expense)
            return
        opex.category = _TYPE_TO_CATEGORY.get(expense.expense_type, expense.expense_type)
        opex.amount = expense.amount
        opex.expense_date = expense.expense_date
        opex.note = expense.note
        logger.debug("expense_sync: updated bi_opex id=%s for expense id=%s", bi_opex_id, expense.id)
    except Exception as exc:
        logger.warning("expense_sync.sync_update failed (non-fatal): %s", exc)


def sync_delete(bi_opex_id: int | None) -> None:
    """Delete the mirror row when an Expense is deleted."""
    if not bi_opex_id:
        return
    try:
        OperatingExpense = _get_opex_model()
        opex = db.session.get(OperatingExpense, bi_opex_id)
        if opex:
            db.session.delete(opex)
            logger.debug("expense_sync: deleted bi_opex id=%s", bi_opex_id)
    except Exception as exc:
        logger.warning("expense_sync.sync_delete failed (non-fatal): %s", exc)


def backfill() -> int:
    """One-time backfill: sync all existing Expense rows that have no mirror yet.
    Returns the number of rows created."""
    try:
        OperatingExpense = _get_opex_model()
    except Exception:
        return 0

    # Check the bi_opex_id column actually exists before querying it
    from sqlalchemy import inspect as _inspect
    try:
        cols = [c["name"] for c in _inspect(db.engine).get_columns("expenses")]
        if "bi_opex_id" not in cols:
            return 0
    except Exception:
        return 0

    expenses = db.session.execute(db.select(Expense)).scalars().all()
    created = 0
    for expense in expenses:
        bi_opex_id = getattr(expense, "bi_opex_id", None)
        if bi_opex_id:
            if db.session.get(OperatingExpense, bi_opex_id):
                continue
        sync_create(expense)
        created += 1
    if created:
        db.session.commit()
    logger.info("expense_sync.backfill: created %d mirror rows", created)
    return created
