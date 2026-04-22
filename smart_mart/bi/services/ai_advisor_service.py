from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func

from ...extensions import db
from ...models.product import Product
from ...models.sale import Sale, SaleItem
from ..utils import as_decimal, decimal_to_float


class AIAdvisorService:
    @staticmethod
    def analyze(
        *,
        low_margin_threshold: float = 0.10,
        dead_stock_days: int = 30,
        overstock_qty_threshold: int = 100,
        low_movement_days: int = 30,
        low_movement_sales_qty: int = 5,
    ) -> list[dict]:
        insights: list[dict] = []

        products = db.session.execute(db.select(Product)).scalars().all()
        today = date.today()
        dead_stock_cutoff = today - timedelta(days=max(1, dead_stock_days))
        movement_cutoff = today - timedelta(days=max(1, low_movement_days))

        sold_recent = db.session.execute(
            db.select(
                SaleItem.product_id,
                func.coalesce(func.sum(SaleItem.quantity), 0).label("qty"),
                func.coalesce(func.sum(SaleItem.subtotal), 0).label("revenue"),
                func.coalesce(func.sum(SaleItem.quantity * SaleItem.cost_price), 0).label("cogs"),
                func.max(func.date(Sale.sale_date)).label("last_sale_date"),
            )
            .join(Sale, Sale.id == SaleItem.sale_id)
            .group_by(SaleItem.product_id)
        ).all()
        by_product = {r.product_id: r for r in sold_recent}

        for product in products:
            cost = as_decimal(product.cost_price or 0)
            price = as_decimal(product.selling_price or 0)
            if price > 0:
                margin = (price - cost) / price
                if margin < as_decimal(low_margin_threshold):
                    insights.append(
                        {
                            "type": "low_margin",
                            "product_id": product.id,
                            "product_name": product.name,
                            "sku": product.sku,
                            "message": f"Low margin on '{product.name}'",
                            "action": "increase_price",
                            "suggested_change": round(float(as_decimal(low_margin_threshold) - margin), 4),
                            "current_margin_pct": round(float(margin) * 100, 2),
                        }
                    )

            stats = by_product.get(product.id)
            if stats:
                revenue = as_decimal(stats.revenue)
                cogs = as_decimal(stats.cogs)
                if revenue < cogs:
                    insights.append(
                        {
                            "type": "loss_product",
                            "product_id": product.id,
                            "product_name": product.name,
                            "sku": product.sku,
                            "message": f"'{product.name}' is running at gross loss",
                            "action": "review_pricing_or_cost",
                            "suggested_change": round(float((cogs - revenue) / cogs), 4) if cogs > 0 else 0.0,
                        }
                    )

                last_sale = stats.last_sale_date
                if isinstance(last_sale, str):
                    last_sale = date.fromisoformat(last_sale)
                if last_sale and last_sale < dead_stock_cutoff:
                    insights.append(
                        {
                            "type": "dead_stock",
                            "product_id": product.id,
                            "product_name": product.name,
                            "sku": product.sku,
                            "message": f"'{product.name}' — no sales in {dead_stock_days}+ days",
                            "action": "discount_or_bundle",
                            "suggested_change": 0.15,
                            "last_sale_date": last_sale.isoformat() if last_sale else None,
                        }
                    )

                recent_qty = db.session.execute(
                    db.select(func.coalesce(func.sum(SaleItem.quantity), 0))
                    .join(Sale, Sale.id == SaleItem.sale_id)
                    .where(SaleItem.product_id == product.id, func.date(Sale.sale_date) >= movement_cutoff)
                ).scalar() or 0
                if int(product.quantity or 0) >= overstock_qty_threshold and int(recent_qty) <= low_movement_sales_qty:
                    insights.append(
                        {
                            "type": "overstock",
                            "product_id": product.id,
                            "product_name": product.name,
                            "sku": product.sku,
                            "message": f"'{product.name}' — high stock ({product.quantity} units), low movement",
                            "action": "run_promotion",
                            "suggested_change": 0.1,
                            "stock_qty": int(product.quantity or 0),
                            "recent_sales_qty": int(recent_qty),
                        }
                    )
            else:
                if int(product.quantity or 0) > 0:
                    insights.append(
                        {
                            "type": "dead_stock",
                            "product_id": product.id,
                            "product_name": product.name,
                            "sku": product.sku,
                            "message": f"'{product.name}' — no sales history",
                            "action": "discount_or_bundle",
                            "suggested_change": 0.2,
                            "last_sale_date": None,
                        }
                    )

        return insights
