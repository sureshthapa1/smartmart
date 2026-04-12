"""Inventory management service — products and stock adjustments."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_, func
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models.product import Product
from ..models.stock_movement import StockMovement


def create_product(data: dict) -> Product:
    """Create a new product. Raises ValueError on duplicate SKU."""
    product = Product(
        name=data["name"],
        category=data.get("category"),
        sku=data["sku"],
        cost_price=data["cost_price"],
        selling_price=data["selling_price"],
        quantity=data.get("quantity", 0),
        supplier_id=data.get("supplier_id"),
        expiry_date=data.get("expiry_date"),
        image_filename=data.get("image_filename"),
        unit=data.get("unit", "pcs"),
    )
    db.session.add(product)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ValueError(f"A product with SKU '{data['sku']}' already exists.")
    # Audit log
    try:
        from . import audit_service
        audit_service.log("create", "Product", product.id, product.name,
                          changes={"name": [None, product.name], "sku": [None, product.sku]})
        db.session.commit()
    except Exception:
        pass
    return product


def update_product(product_id: int, data: dict) -> Product:
    """Update fields on an existing product."""
    product: Product = db.get_or_404(Product, product_id)
    updatable = ("name", "category", "sku", "cost_price", "selling_price",
                 "quantity", "supplier_id", "expiry_date", "image_filename", "unit", "reorder_point")
    for field in updatable:
        if field in data:
            setattr(product, field, data[field])
    product.updated_at = datetime.now(timezone.utc)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ValueError(f"A product with SKU '{data.get('sku')}' already exists.")
    # Audit log
    try:
        from . import audit_service
        audit_service.log("update", "Product", product.id, product.name)
        db.session.commit()
    except Exception:
        pass
    return product


def delete_product(product_id: int) -> None:
    """Delete a product. Raises ValueError if it has sale/purchase records."""
    product: Product = db.get_or_404(Product, product_id)
    if product.sale_items.count() > 0:
        raise ValueError(f"Cannot delete '{product.name}': has associated sale records.")
    if product.purchase_items.count() > 0:
        raise ValueError(f"Cannot delete '{product.name}': has associated purchase records.")
    db.session.delete(product)
    db.session.commit()


def get_products(search: str | None = None, page: int = 1, per_page: int = 100) -> list[Product]:
    """Return paginated products with optional search by name/category/SKU."""
    stmt = db.select(Product).order_by(Product.name)
    if search:
        term = search.strip().lower()
        stmt = stmt.where(
            or_(
                func.lower(Product.name).contains(term),
                func.lower(Product.category).contains(term),
                func.lower(Product.sku) == term,
            )
        )
    offset = (page - 1) * per_page
    stmt = stmt.limit(per_page).offset(offset)
    return db.session.execute(stmt).scalars().all()


def adjust_stock(product_id: int, qty: int, direction: str, note: str, user_id: int,
                 adjustment_type: str = None) -> StockMovement:
    """Manually adjust stock in or out. Raises ValueError on invalid direction or insufficient stock.
    
    adjustment_type options for 'out': 'damage', 'loss', 'theft', 'expiry', 'adjustment_out'
    """
    if direction not in ("in", "out"):
        raise ValueError("direction must be 'in' or 'out'.")
    # Determine change_type
    if direction == "out":
        valid_out_types = ("damage", "loss", "theft", "expiry", "adjustment_out")
        change_type = adjustment_type if adjustment_type in valid_out_types else "adjustment_out"
    else:
        change_type = "adjustment_in"
    try:
        product: Product = db.get_or_404(Product, product_id)
        if direction == "out":
            if qty > product.quantity:
                raise ValueError(
                    f"Insufficient stock: requested {qty}, available {product.quantity}."
                )
            product.quantity -= qty
            change_amount = -qty
        else:
            product.quantity += qty
            change_amount = qty
        product.updated_at = datetime.now(timezone.utc)
        movement = StockMovement(
            product_id=product_id,
            change_amount=change_amount,
            change_type=change_type,
            note=note,
            created_by=user_id,
            timestamp=datetime.now(timezone.utc),
        )
        db.session.add(movement)
        # Audit log
        try:
            from . import audit_service
            audit_service.log(
                action="update",
                entity_type="Product",
                entity_id=product_id,
                entity_label=product.name,
                changes={"quantity": [str(product.quantity - change_amount), str(product.quantity)],
                         "adjustment_type": [None, change_type]},
            )
        except Exception:
            pass
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return movement
