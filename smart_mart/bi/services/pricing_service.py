from __future__ import annotations

from decimal import Decimal
from decimal import ROUND_HALF_UP

from ...extensions import db
from ...models.product import Product
from ..models.pricing import CategoryMarginRule
from ..utils import as_decimal, decimal_to_float, money


class PricingService:
    DEFAULT_MARGIN_PCT = Decimal("20")

    @staticmethod
    def upsert_margin_rule(category: str, margin_pct: object, rounding_base: int = 1) -> CategoryMarginRule:
        category_key = (category or "").strip().lower()
        if not category_key:
            raise ValueError("category is required")

        rule = db.session.execute(
            db.select(CategoryMarginRule).where(CategoryMarginRule.category == category_key)
        ).scalar_one_or_none()

        if rule is None:
            rule = CategoryMarginRule(category=category_key, margin_pct=as_decimal(margin_pct), rounding_base=max(1, int(rounding_base or 1)))
            db.session.add(rule)
        else:
            rule.margin_pct = as_decimal(margin_pct)
            rule.rounding_base = max(1, int(rounding_base or 1))

        db.session.commit()
        return rule

    @staticmethod
    def suggest_price(
        *,
        product_id: int | None = None,
        cost: object | None = None,
        category: str | None = None,
        margin_pct: object | None = None,
        rounding_base: int | None = None,
    ) -> dict:
        product = db.session.get(Product, int(product_id)) if product_id else None
        if product_id and product is None:
            raise ValueError(f"Product {product_id} not found")

        base_cost = as_decimal(cost if cost is not None else (product.cost_price if product else 0))
        if base_cost <= 0:
            raise ValueError("cost must be > 0")

        category_name = (category or (product.category if product else "") or "").strip().lower()
        rule = None
        if category_name:
            rule = db.session.execute(
                db.select(CategoryMarginRule).where(CategoryMarginRule.category == category_name)
            ).scalar_one_or_none()

        applied_margin = as_decimal(margin_pct) if margin_pct is not None else as_decimal(rule.margin_pct if rule else PricingService.DEFAULT_MARGIN_PCT)
        applied_rounding = max(1, int(rounding_base if rounding_base is not None else (rule.rounding_base if rule else 1)))

        raw_price = base_cost * (Decimal("1") + (applied_margin / Decimal("100")))
        rounded_price = PricingService._round_to_base(raw_price, applied_rounding)

        return {
            "product_id": product.id if product else None,
            "category": category_name or None,
            "cost": decimal_to_float(base_cost),
            "margin_pct": decimal_to_float(applied_margin),
            "rounding_base": applied_rounding,
            "suggested_price": decimal_to_float(rounded_price),
        }

    @staticmethod
    def _round_to_base(price: Decimal, base: int) -> Decimal:
        if base <= 1:
            return money(price)
        quotient = as_decimal(price) / as_decimal(base)
        nearest = quotient.to_integral_value(rounding=ROUND_HALF_UP)
        return money(nearest * as_decimal(base))
