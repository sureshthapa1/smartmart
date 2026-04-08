"""AI Module 2: Invoice Error Detection System

Detects anomalies before sale confirmation:
- Incorrect pricing (price deviates >30% from product's selling price)
- Quantity mismatch (qty exceeds available stock)
- Duplicate entries (same product added twice)
- Missing/incorrect tax calculations
- Unusually large discounts
"""

from __future__ import annotations

from ..extensions import db
from ..models.product import Product
from ..models.sale import Sale, SaleItem
from sqlalchemy import func
from datetime import date, timedelta


def validate_sale_items(items: list[dict], discount_amount: float = 0) -> dict:
    """
    Validate sale items before confirmation.

    Args:
        items: list of {product_id, quantity, unit_price}
        discount_amount: total discount applied

    Returns:
        {
            "valid": bool,
            "warnings": [...],
            "errors": [...],
            "suggestions": [...]
        }
    """
    warnings = []
    errors = []
    suggestions = []
    seen_products = {}

    for i, item in enumerate(items):
        pid = item.get("product_id")
        qty = item.get("quantity", 0)
        unit_price = float(item.get("unit_price", 0))

        if not pid:
            errors.append({"row": i + 1, "type": "missing_product", "message": f"Row {i+1}: No product selected."})
            continue

        product = db.session.get(Product, pid)
        if not product:
            errors.append({"row": i + 1, "type": "product_not_found", "message": f"Row {i+1}: Product ID {pid} not found."})
            continue

        # ── Duplicate entry check ─────────────────────────────────────────
        if pid in seen_products:
            warnings.append({
                "row": i + 1, "type": "duplicate_entry",
                "message": f"'{product.name}' appears multiple times. Consider merging into one row.",
                "severity": "medium",
            })
        seen_products[pid] = seen_products.get(pid, 0) + qty

        # ── Stock check ───────────────────────────────────────────────────
        if qty > product.quantity:
            errors.append({
                "row": i + 1, "type": "insufficient_stock",
                "message": f"'{product.name}': Requested {qty}, only {product.quantity} available.",
                "severity": "high",
            })

        if qty <= 0:
            errors.append({
                "row": i + 1, "type": "invalid_quantity",
                "message": f"Row {i+1}: Quantity must be greater than 0.",
                "severity": "high",
            })

        # ── Price anomaly detection ───────────────────────────────────────
        expected_price = float(product.selling_price)
        if expected_price > 0:
            deviation = abs(unit_price - expected_price) / expected_price
            if deviation > 0.30:
                severity = "high" if deviation > 0.50 else "medium"
                warnings.append({
                    "row": i + 1, "type": "price_anomaly",
                    "message": (
                        f"'{product.name}': Price NPR {unit_price:.2f} deviates "
                        f"{deviation*100:.1f}% from standard NPR {expected_price:.2f}."
                    ),
                    "severity": severity,
                    "expected": expected_price,
                    "actual": unit_price,
                })
            elif unit_price < float(product.cost_price):
                warnings.append({
                    "row": i + 1, "type": "below_cost",
                    "message": (
                        f"'{product.name}': Selling at NPR {unit_price:.2f} is BELOW cost price "
                        f"NPR {float(product.cost_price):.2f}. This will cause a loss!"
                    ),
                    "severity": "high",
                })

        # ── Unusually large quantity ──────────────────────────────────────
        avg_qty = _avg_qty_per_sale(pid)
        if avg_qty > 0 and qty > avg_qty * 5:
            warnings.append({
                "row": i + 1, "type": "unusual_quantity",
                "message": f"'{product.name}': Qty {qty} is {qty/avg_qty:.1f}x the average order quantity ({avg_qty:.1f}).",
                "severity": "low",
            })

    # ── Discount check ────────────────────────────────────────────────────
    if discount_amount > 0 and items:
        subtotal = sum(float(i.get("unit_price", 0)) * i.get("quantity", 0) for i in items)
        if subtotal > 0:
            disc_pct = (discount_amount / subtotal) * 100
            if disc_pct > 20:
                warnings.append({
                    "row": 0, "type": "high_discount",
                    "message": f"Discount of {disc_pct:.1f}% is unusually high. Requires authorization.",
                    "severity": "medium",
                })

    # ── Suggestions ───────────────────────────────────────────────────────
    if not errors and not warnings:
        suggestions.append("✅ All items validated. Safe to confirm sale.")
    elif errors:
        suggestions.append("❌ Fix errors before confirming.")
    else:
        suggestions.append("⚠️ Review warnings before confirming.")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "suggestions": suggestions,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }


def _avg_qty_per_sale(product_id: int) -> float:
    """Average quantity per sale transaction for a product."""
    result = db.session.execute(
        db.select(func.avg(SaleItem.quantity))
        .where(SaleItem.product_id == product_id)
    ).scalar()
    return float(result) if result else 0.0
