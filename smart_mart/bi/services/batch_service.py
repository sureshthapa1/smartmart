from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from ...extensions import db
from ...models.product import Product
from ..models.purchase_batch import PurchaseBatch, PurchaseBatchExpense, PurchaseBatchItem
from ..utils import as_decimal, decimal_to_float, money, quantize_unit_cost
from .inventory_service import InventoryService


class BatchService:
    VALID_METHODS = {"quantity", "value"}

    @classmethod
    def create_batch(
        cls,
        *,
        purchase_date: date,
        supplier_name: str | None,
        allocation_method: str,
        items: list[dict],
        expenses: list[dict] | None = None,
    ) -> PurchaseBatch:
        method = allocation_method.lower().strip()
        if method not in cls.VALID_METHODS:
            raise ValueError("allocation_method must be 'quantity' or 'value'")
        if not items:
            raise ValueError("At least one batch item is required")

        batch = PurchaseBatch(
            supplier_name=supplier_name,
            purchase_date=purchase_date,
            allocation_method=method,
            status="draft",
        )
        db.session.add(batch)
        db.session.flush()

        for item in items:
            product = db.session.get(Product, int(item["product_id"]))
            if product is None:
                raise ValueError(f"Product {item['product_id']} not found")
            qty = int(item["quantity"])
            if qty <= 0:
                raise ValueError("Batch item quantity must be > 0")
            purchase_price = quantize_unit_cost(item["purchase_price"])
            db.session.add(
                PurchaseBatchItem(
                    batch_id=batch.id,
                    product_id=product.id,
                    quantity=qty,
                    purchase_price=purchase_price,
                    final_cost=purchase_price,
                )
            )

        for ex in expenses or []:
            amount = money(ex.get("amount", 0))
            if amount <= 0:
                continue
            db.session.add(
                PurchaseBatchExpense(
                    batch_id=batch.id,
                    expense_type=(ex.get("expense_type") or "other").strip().lower(),
                    amount=amount,
                )
            )

        cls.recalculate_allocation(batch.id)
        db.session.commit()
        return batch

    @classmethod
    def add_items(cls, batch_id: int, items: list[dict]) -> PurchaseBatch:
        batch = cls._get_batch(batch_id)
        cls._ensure_draft(batch)
        for item in items:
            product = db.session.get(Product, int(item["product_id"]))
            if product is None:
                raise ValueError(f"Product {item['product_id']} not found")
            qty = int(item["quantity"])
            if qty <= 0:
                raise ValueError("Batch item quantity must be > 0")
            purchase_price = quantize_unit_cost(item["purchase_price"])
            db.session.add(
                PurchaseBatchItem(
                    batch_id=batch.id,
                    product_id=product.id,
                    quantity=qty,
                    purchase_price=purchase_price,
                    final_cost=purchase_price,
                )
            )
        cls.recalculate_allocation(batch_id)
        db.session.commit()
        return batch

    @classmethod
    def add_expenses(cls, batch_id: int, expenses: list[dict]) -> PurchaseBatch:
        batch = cls._get_batch(batch_id)
        cls._ensure_draft(batch)
        for ex in expenses:
            amount = money(ex.get("amount", 0))
            if amount <= 0:
                continue
            db.session.add(
                PurchaseBatchExpense(
                    batch_id=batch.id,
                    expense_type=(ex.get("expense_type") or "other").strip().lower(),
                    amount=amount,
                )
            )
        cls.recalculate_allocation(batch_id)
        db.session.commit()
        return batch

    # Feature 2: remove a single item from a draft batch
    @classmethod
    def remove_item(cls, batch_id: int, item_id: int) -> PurchaseBatch:
        batch = cls._get_batch(batch_id)
        cls._ensure_draft(batch)
        item = db.session.get(PurchaseBatchItem, item_id)
        if item is None or item.batch_id != batch_id:
            raise ValueError(f"Item {item_id} not found in batch {batch_id}")
        db.session.delete(item)
        db.session.flush()
        # Reload batch items after deletion
        remaining = list(batch.items)
        if remaining:
            cls.recalculate_allocation(batch_id)
        else:
            # No items left — reset totals
            batch.subtotal_amount = money(0)
            batch.shared_expense_total = money(
                sum(as_decimal(ex.amount) for ex in batch.expenses)
            )
            batch.grand_total = batch.shared_expense_total
            batch.allocation_snapshot = None
            db.session.flush()
        db.session.commit()
        return batch

    # Feature 2: remove a batch expense
    @classmethod
    def remove_expense(cls, batch_id: int, expense_id: int) -> PurchaseBatch:
        batch = cls._get_batch(batch_id)
        cls._ensure_draft(batch)
        expense = db.session.get(PurchaseBatchExpense, expense_id)
        if expense is None or expense.batch_id != batch_id:
            raise ValueError(f"Expense {expense_id} not found in batch {batch_id}")
        db.session.delete(expense)
        db.session.flush()
        if list(batch.items):
            cls.recalculate_allocation(batch_id)
        db.session.commit()
        return batch

    @classmethod
    def recalculate_allocation(cls, batch_id: int) -> PurchaseBatch:
        batch = cls._get_batch(batch_id)
        cls._ensure_draft(batch)

        items = list(batch.items)
        if not items:
            raise ValueError("Cannot allocate expenses for a batch with no items")

        shared_total = sum((as_decimal(ex.amount) for ex in batch.expenses), Decimal("0"))
        basis_map = cls._build_basis_map(items, batch.allocation_method)
        allocated_map, allocation_steps = cls._allocate(shared_total, basis_map)

        subtotal = Decimal("0")
        for item in items:
            allocated_total = allocated_map[item.id]
            per_unit_allocated = quantize_unit_cost(allocated_total / as_decimal(item.quantity))
            final_cost = quantize_unit_cost(as_decimal(item.purchase_price) + per_unit_allocated)
            item.allocated_total = money(allocated_total)
            item.allocated_cost_per_unit = per_unit_allocated
            item.final_cost = final_cost
            item.allocation_detail = {
                "basis": decimal_to_float(basis_map[item.id]),
                "allocated_total": decimal_to_float(item.allocated_total),
                "allocated_cost_per_unit": decimal_to_float(item.allocated_cost_per_unit),
                "final_cost": decimal_to_float(item.final_cost),
            }
            subtotal += as_decimal(item.purchase_price) * as_decimal(item.quantity)

        batch.subtotal_amount = money(subtotal)
        batch.shared_expense_total = money(shared_total)
        batch.grand_total = money(subtotal + shared_total)
        batch.allocation_snapshot = {
            "calculated_at": datetime.now(timezone.utc).isoformat(),
            "allocation_method": batch.allocation_method,
            "shared_expense_total": decimal_to_float(batch.shared_expense_total),
            "steps": allocation_steps,
            "items": [
                {
                    "batch_item_id": item.id,
                    "product_id": item.product_id,
                    "quantity": item.quantity,
                    "purchase_price": decimal_to_float(item.purchase_price),
                    "allocated_total": decimal_to_float(item.allocated_total),
                    "allocated_cost_per_unit": decimal_to_float(item.allocated_cost_per_unit),
                    "final_cost": decimal_to_float(item.final_cost),
                }
                for item in items
            ],
        }
        db.session.flush()
        return batch

    @classmethod
    def finalize_batch(cls, batch_id: int) -> PurchaseBatch:
        batch = cls._get_batch(batch_id)
        cls._ensure_draft(batch)

        try:
            with db.session.begin_nested():
                cls.recalculate_allocation(batch_id)
                for item in batch.items:
                    product = InventoryService.get_product(item.product_id)
                    InventoryService.apply_weighted_average_cost(
                        product=product,
                        incoming_qty=int(item.quantity),
                        incoming_unit_cost=as_decimal(item.final_cost),
                    )
                    InventoryService.add_ledger_entry(
                        product_id=product.id,
                        movement_type="purchase_in",
                        qty=int(item.quantity),
                        unit_cost=as_decimal(item.final_cost),
                        reference_type="purchase_batch",
                        reference_id=batch.id,
                    )

                batch.status = "finalized"
                batch.finalized_at = datetime.now(timezone.utc)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        return batch

    @staticmethod
    def _build_basis_map(items: list[PurchaseBatchItem], method: str) -> dict[int, Decimal]:
        basis_map: dict[int, Decimal] = {}
        for item in items:
            qty = as_decimal(item.quantity)
            if method == "quantity":
                basis = qty
            else:
                basis = qty * as_decimal(item.purchase_price)
            basis_map[item.id] = basis
        return basis_map

    @staticmethod
    def _allocate(total: Decimal, basis_map: dict[int, Decimal]) -> tuple[dict[int, Decimal], list[dict]]:
        total = money(total)
        basis_total = sum(basis_map.values(), Decimal("0"))
        if total == 0 or basis_total <= 0:
            return ({item_id: Decimal("0.00") for item_id in basis_map}, [])

        provisional: list[tuple[int, Decimal, Decimal, Decimal]] = []
        for item_id, basis in basis_map.items():
            raw = total * (basis / basis_total)
            rounded = money(raw)
            fractional = raw - rounded
            provisional.append((item_id, basis, raw, fractional))

        rounded_map = {item_id: money(raw) for item_id, _, raw, _ in provisional}
        current_sum = sum(rounded_map.values(), Decimal("0"))
        diff = money(total - current_sum)

        steps: list[dict] = []
        if diff != 0:
            cent = Decimal("0.01") if diff > 0 else Decimal("-0.01")
            required = int((diff.copy_abs() / Decimal("0.01")))
            order = sorted(provisional, key=lambda x: x[3], reverse=(diff > 0))
            for i in range(required):
                item_id = order[i % len(order)][0]
                rounded_map[item_id] = money(rounded_map[item_id] + cent)
                steps.append({"item_id": item_id, "adjustment": float(cent)})

        return rounded_map, steps

    @staticmethod
    def _get_batch(batch_id: int) -> PurchaseBatch:
        batch = db.session.get(PurchaseBatch, batch_id)
        if batch is None:
            raise ValueError(f"Batch {batch_id} not found")
        return batch

    @staticmethod
    def _ensure_draft(batch: PurchaseBatch) -> None:
        if batch.status != "draft":
            raise ValueError("Batch is locked and already finalized")
