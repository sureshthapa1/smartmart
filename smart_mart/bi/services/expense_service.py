from __future__ import annotations

from datetime import date

from ...extensions import db
from ..models.inventory_ledger import InventoryLedgerEntry
from ..models.operating_expense import OperatingExpense
from ..utils import decimal_to_float, money


def _serialize_expense(row: OperatingExpense) -> dict:
    return {
        "id": row.id,
        "category": row.category,
        "amount": decimal_to_float(row.amount),
        "date": row.expense_date.isoformat(),
        "payment_method": row.payment_method,
        "note": row.note,
        "product_id": row.product_id,
    }


class ExpenseService:
    @staticmethod
    def create_opex(data: dict) -> OperatingExpense:
        expense = OperatingExpense(
            category=(data.get("category") or "misc").strip().lower(),
            amount=money(data.get("amount") or 0),
            expense_date=date.fromisoformat(data.get("date")) if data.get("date") else date.today(),
            payment_method=(data.get("payment_method") or "cash").strip().lower(),
            note=data.get("note"),
            product_id=data.get("product_id") or None,
        )
        if expense.amount <= 0:
            raise ValueError("amount must be > 0")
        db.session.add(expense)
        db.session.commit()
        return expense

    # FIX 4: expense update
    @staticmethod
    def update_opex(expense_id: int, data: dict) -> OperatingExpense:
        expense = db.session.get(OperatingExpense, expense_id)
        if expense is None:
            raise ValueError(f"Expense {expense_id} not found")
        if "category" in data and data["category"]:
            expense.category = data["category"].strip().lower()
        if "amount" in data:
            new_amount = money(data["amount"])
            if new_amount <= 0:
                raise ValueError("amount must be > 0")
            expense.amount = new_amount
        if "date" in data and data["date"]:
            expense.expense_date = date.fromisoformat(data["date"])
        if "payment_method" in data and data["payment_method"]:
            expense.payment_method = data["payment_method"].strip().lower()
        if "note" in data:
            expense.note = data.get("note")
        if "product_id" in data:
            expense.product_id = data.get("product_id") or None
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
        return [_serialize_expense(row) for row in rows]

    # FIX 5: inventory ledger read — reads from StockMovement (always populated)
    # bi_inventory_ledger is only populated via BI batch finalization;
    # StockMovement is the reliable source for all stock changes.
    @staticmethod
    def list_ledger(
        product_id: int | None = None,
        movement_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        from ...models.product import Product
        from ...models.stock_movement import StockMovement

        # Map StockMovement.change_type to ledger-style movement_type labels
        _TYPE_MAP = {
            "sale":           "sale_out",
            "purchase":       "purchase_in",
            "adjustment_in":  "adjustment_in",
            "adjustment_out": "adjustment_out",
            "return":         "return_in",
            "transfer_in":    "transfer_in",
            "transfer_out":   "transfer_out",
        }

        stmt = (
            db.select(StockMovement, Product.name.label("product_name"), Product.sku.label("sku"))
            .join(Product, Product.id == StockMovement.product_id)
            .order_by(StockMovement.timestamp.desc(), StockMovement.id.desc())
        )
        if product_id:
            stmt = stmt.where(StockMovement.product_id == product_id)
        if movement_type:
            # reverse-map the ledger label back to StockMovement.change_type
            _REV = {v: k for k, v in _TYPE_MAP.items()}
            raw_type = _REV.get(movement_type, movement_type)
            stmt = stmt.where(StockMovement.change_type == raw_type)

        total = db.session.execute(
            db.select(db.func.count()).select_from(stmt.subquery())
        ).scalar() or 0

        rows = db.session.execute(stmt.offset(offset).limit(limit)).all()

        entries = []
        for row in rows:
            sm = row.StockMovement
            change_type = sm.change_type or ""
            mv_type = _TYPE_MAP.get(change_type, change_type)
            qty = int(sm.change_amount or 0)
            entries.append({
                "id": sm.id,
                "product_id": sm.product_id,
                "product_name": row.product_name,
                "sku": row.sku,
                "movement_type": mv_type,
                "qty": qty,
                "unit_cost": None,   # StockMovement doesn't store unit cost
                "reference_type": change_type,
                "reference_id": sm.reference_id,
                "movement_date": sm.timestamp.isoformat() if sm.timestamp else None,
            })

        return {"entries": entries, "total": total, "limit": limit, "offset": offset}
