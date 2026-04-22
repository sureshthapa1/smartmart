from __future__ import annotations

from datetime import datetime, timezone

from flask import current_app

from ...extensions import db
from ...models.sale import Sale, SaleItem
from ...models.user import User
from ..utils import as_decimal, decimal_to_float, money
from .inventory_service import InventoryService


class SalesService:
    @staticmethod
    def create_sale(payload: dict, user_id: int | None = None) -> Sale:
        items = payload.get("items") or []
        if not items:
            raise ValueError("Sale requires at least one item")

        prevent_negative_stock = bool(current_app.config.get("SMARTMART_PREVENT_NEGATIVE_STOCK", True))
        resolved_user_id = SalesService._resolve_user_id(user_id)

        product_rows: list[tuple[object, int, object]] = []
        total_amount = as_decimal(0)

        for row in items:
            product = InventoryService.get_product(int(row["product_id"]))
            qty = int(row["quantity"])
            if qty <= 0:
                raise ValueError("Sale quantity must be > 0")
            sell_price = money(row["selling_price"])
            InventoryService.ensure_stock(product, qty, prevent_negative_stock)
            product_rows.append((product, qty, sell_price))
            total_amount += as_decimal(sell_price) * as_decimal(qty)

        sale = Sale(
            user_id=resolved_user_id,
            total_amount=money(total_amount),
            sale_date=datetime.now(timezone.utc),
            customer_name=payload.get("customer_name"),
            payment_mode=(payload.get("payment_mode") or "cash").lower(),
        )

        try:
            with db.session.begin_nested():
                db.session.add(sale)
                db.session.flush()

                for product, qty, sell_price in product_rows:
                    cost_snapshot = as_decimal(product.cost_price or 0)
                    db.session.add(
                        SaleItem(
                            sale_id=sale.id,
                            product_id=product.id,
                            quantity=qty,
                            unit_price=sell_price,
                            cost_price=cost_snapshot,
                            subtotal=money(as_decimal(sell_price) * as_decimal(qty)),
                        )
                    )
                    InventoryService.reduce_stock_for_sale(product, qty)
                    InventoryService.add_ledger_entry(
                        product_id=product.id,
                        movement_type="sale_out",
                        qty=-qty,
                        unit_cost=cost_snapshot,
                        reference_type="sale",
                        reference_id=sale.id,
                    )
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        return sale

    @staticmethod
    def serialize_sale(sale: Sale) -> dict:
        return {
            "sale_id": sale.id,
            "date": sale.sale_date.isoformat() if sale.sale_date else None,
            "total_amount": decimal_to_float(sale.total_amount),
            "items": [
                {
                    "product_id": item.product_id,
                    "quantity": item.quantity,
                    "selling_price": decimal_to_float(item.unit_price),
                    "cost_price_snapshot": decimal_to_float(item.cost_price or 0),
                    "subtotal": decimal_to_float(item.subtotal),
                }
                for item in sale.items
            ],
        }

    @staticmethod
    def _resolve_user_id(user_id: int | None) -> int:
        if user_id:
            user = db.session.get(User, int(user_id))
            if user is None:
                raise ValueError(f"User {user_id} not found")
            return user.id

        first_user = db.session.execute(db.select(User).order_by(User.id.asc())).scalars().first()
        if first_user is None:
            raise ValueError("No users available. Create a user before recording sale")
        return first_user.id
