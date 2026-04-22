from __future__ import annotations

from decimal import Decimal

from ...extensions import db
from ...models.product import Product
from ..models.inventory_ledger import InventoryLedgerEntry
from ..utils import as_decimal, money, quantize_unit_cost


class InventoryService:
    @staticmethod
    def get_product(product_id: int) -> Product:
        product = db.session.get(Product, product_id)
        if product is None:
            raise ValueError(f"Product {product_id} not found")
        return product

    @staticmethod
    def ensure_stock(product: Product, requested_qty: int, prevent_negative_stock: bool = True) -> None:
        if prevent_negative_stock and requested_qty > int(product.quantity or 0):
            raise ValueError(
                f"Insufficient stock for '{product.name}': requested={requested_qty}, available={product.quantity}"
            )

    @staticmethod
    def apply_weighted_average_cost(product: Product, incoming_qty: int, incoming_unit_cost: Decimal) -> None:
        incoming_unit_cost = quantize_unit_cost(incoming_unit_cost)
        old_qty = int(product.quantity or 0)
        old_cost = quantize_unit_cost(product.cost_price or 0)
        total_qty = old_qty + incoming_qty
        if total_qty <= 0:
            product.cost_price = quantize_unit_cost(0)
            product.quantity = 0
            product.inventory_value = money(0)
            return

        weighted_cost = ((as_decimal(old_qty) * old_cost) + (as_decimal(incoming_qty) * incoming_unit_cost)) / as_decimal(total_qty)
        product.cost_price = quantize_unit_cost(weighted_cost)
        product.quantity = total_qty
        product.inventory_value = money(as_decimal(product.quantity) * as_decimal(product.cost_price))

    @staticmethod
    def reduce_stock_for_sale(product: Product, qty: int) -> None:
        product.quantity = int(product.quantity or 0) - int(qty)
        product.inventory_value = money(as_decimal(product.quantity) * as_decimal(product.cost_price or 0))

    @staticmethod
    def add_ledger_entry(
        *,
        product_id: int,
        movement_type: str,
        qty: int,
        unit_cost: Decimal,
        reference_type: str,
        reference_id: int,
    ) -> InventoryLedgerEntry:
        entry = InventoryLedgerEntry(
            product_id=product_id,
            movement_type=movement_type,
            qty=qty,
            unit_cost=quantize_unit_cost(unit_cost),
            reference_type=reference_type,
            reference_id=reference_id,
        )
        db.session.add(entry)
        return entry
