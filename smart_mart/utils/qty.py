"""
qty.py — Decimal-safe helpers for mutating Numeric stock columns.

SQLAlchemy maps db.Numeric columns to Python decimal.Decimal.  A plain
  product.quantity += float_value
raises:
  TypeError: unsupported operand type(s) for +=: 'decimal.Decimal' and 'float'

Use qty_add / qty_sub everywhere instead of raw += / -= so the conversion
is handled in one place and the error can never resurface.

Usage:
    from smart_mart.utils.qty import qty_add, qty_sub

    qty_add(product, qty)          # product.quantity += Decimal(qty)
    qty_sub(product, qty)          # product.quantity -= Decimal(qty)
    qty_add(variant, qty)          # works for ProductVariant too
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def _to_decimal(value) -> Decimal:
    """Convert any numeric type to Decimal without precision loss.

    Accepts int, float, str, Decimal, or None (treated as 0).
    Raises TypeError for non-numeric input.
    """
    if value is None:
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        # str() conversion avoids float binary-fraction noise
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation:
            raise TypeError(f"Cannot convert {value!r} to Decimal")
    raise TypeError(f"Cannot convert {type(value).__name__} to Decimal")


def qty_add(model_instance, delta) -> None:
    """Add *delta* to model_instance.quantity using safe Decimal arithmetic.

    Equivalent to:
        model_instance.quantity += delta   # but Decimal-safe
    """
    current = _to_decimal(getattr(model_instance, "quantity", None))
    setattr(model_instance, "quantity", current + _to_decimal(delta))


def qty_sub(model_instance, delta) -> None:
    """Subtract *delta* from model_instance.quantity using safe Decimal arithmetic.

    Equivalent to:
        model_instance.quantity -= delta   # but Decimal-safe
    """
    current = _to_decimal(getattr(model_instance, "quantity", None))
    setattr(model_instance, "quantity", current - _to_decimal(delta))
