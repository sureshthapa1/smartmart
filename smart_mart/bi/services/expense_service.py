from __future__ import annotations

from datetime import date

from ...extensions import db
from ..models.operating_expense import OperatingExpense
from ..utils import decimal_to_float, money


class ExpenseService:
    @staticmethod
    def create_opex(data: dict) -> OperatingExpense:
        expense = OperatingExpense(
            category=(data.get("category") or "misc").strip().lower(),
            amount=money(data.get("amount") or 0),
            expense_date=date.fromisoformat(data.get("date")) if data.get("date") else date.today(),
            payment_method=(data.get("payment_method") or "cash").strip().lower(),
            note=data.get("note"),
        )
        if expense.amount <= 0:
            raise ValueError("amount must be > 0")
        db.session.add(expense)
        db.session.commit()
        return expense

    @staticmethod
    def list_opex(start: date | None = None, end: date | None = None) -> list[dict]:
        stmt = db.select(OperatingExpense).order_by(OperatingExpense.expense_date.desc(), OperatingExpense.id.desc())
        if start:
            stmt = stmt.where(OperatingExpense.expense_date >= start)
        if end:
            stmt = stmt.where(OperatingExpense.expense_date <= end)
        rows = db.session.execute(stmt).scalars().all()
        return [
            {
                "id": row.id,
                "category": row.category,
                "amount": decimal_to_float(row.amount),
                "date": row.expense_date.isoformat(),
                "payment_method": row.payment_method,
                "note": row.note,
            }
            for row in rows
        ]
