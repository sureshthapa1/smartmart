from __future__ import annotations

from ...extensions import db
from ...models.product import Product
from ..utils import as_decimal, decimal_to_float, money, quantize_unit_cost


class ProductService:
    @staticmethod
    def upsert_product(data: dict) -> Product:
        sku = (data.get("sku") or "").strip()
        if not sku:
            raise ValueError("sku is required")

        product = db.session.execute(db.select(Product).where(Product.sku == sku)).scalar_one_or_none()
        if product is None:
            product = Product(
                sku=sku,
                name=data.get("name") or sku,
                category=data.get("category"),
                quantity=int(data.get("quantity") or 0),
                cost_price=quantize_unit_cost(data.get("cost_price") or 0),
                selling_price=money(data.get("selling_price") or 0),
                unit=data.get("unit") or "pcs",
            )
            product.inventory_value = money(as_decimal(product.quantity) * as_decimal(product.cost_price))
            db.session.add(product)
        else:
            if "name" in data:
                product.name = data["name"]
            if "category" in data:
                product.category = data["category"]
            if "selling_price" in data:
                product.selling_price = money(data["selling_price"])
            if "unit" in data:
                product.unit = data["unit"]
            product.inventory_value = money(as_decimal(product.quantity or 0) * as_decimal(product.cost_price or 0))

        db.session.commit()
        return product

    @staticmethod
    def list_products() -> list[dict]:
        rows = db.session.execute(db.select(Product).order_by(Product.name.asc())).scalars().all()
        return [
            {
                "id": p.id,
                "name": p.name,
                "sku": p.sku,
                "category": p.category,
                "quantity": int(p.quantity or 0),
                "cost_price": decimal_to_float(p.cost_price or 0),
                "selling_price": decimal_to_float(p.selling_price or 0),
                "inventory_value": decimal_to_float(p.inventory_value or 0),
            }
            for p in rows
        ]
