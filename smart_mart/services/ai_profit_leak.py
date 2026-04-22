"""AI Module 3: Profit Leak Detection AI

Analyzes sales and purchase data to identify:
- Low margin products
- Frequent discount losses
- Inventory shrinkage patterns
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, func

from ..extensions import db
from ..models.product import Product
from ..models.sale import Sale, SaleItem
from ..models.sale_return import SaleReturn
from ..models.stock_movement import StockMovement
from ..models.expense import Expense
from ..models.user import User


def low_margin_products(margin_threshold: float = 15.0) -> dict:
    """Identify products with gross margin below threshold (%)."""
    products = db.session.execute(db.select(Product)).scalars().all()
    leaks = []
    for p in products:
        if float(p.selling_price) <= 0:
            continue
        margin = ((float(p.selling_price) - float(p.cost_price)) / float(p.selling_price)) * 100
        if margin < margin_threshold:
            # Total revenue lost vs ideal 20% margin
            qty_sold = db.session.execute(
                db.select(func.coalesce(func.sum(SaleItem.quantity), 0))
                .where(SaleItem.product_id == p.id)
            ).scalar() or 0
            ideal_margin = 0.20
            actual_margin_pct = margin / 100
            revenue_lost = float(p.selling_price) * qty_sold * (ideal_margin - actual_margin_pct)
            leaks.append({
                "id": p.id,
                "name": p.name,
                "sku": p.sku,
                "cost_price": float(p.cost_price),
                "selling_price": float(p.selling_price),
                "margin_pct": round(margin, 2),
                "qty_sold_total": qty_sold,
                "estimated_revenue_lost": round(max(0, revenue_lost), 2),
                "recommendation": (
                    f"Increase price to NPR {float(p.cost_price) / 0.80:.2f} for 20% margin"
                    if margin < 0 else f"Consider raising price by NPR {(float(p.cost_price) * 0.20):.2f}"
                ),
            })

    leaks.sort(key=lambda x: x["margin_pct"])
    total_lost = sum(l["estimated_revenue_lost"] for l in leaks)

    return {
        "threshold_pct": margin_threshold,
        "low_margin_products": leaks,
        "total_products_affected": len(leaks),
        "total_estimated_revenue_lost": round(total_lost, 2),
        "insight": (
            f"{len(leaks)} products below {margin_threshold}% margin. "
            f"Estimated NPR {total_lost:,.0f} in lost profit opportunity."
        ) if leaks else "All products have healthy margins.",
    }


def discount_loss_analysis(days: int = 30) -> dict:
    """Analyze discount losses over a period."""
    start = date.today() - timedelta(days=days)
    sales = db.session.execute(
        db.select(Sale)
        .where(func.date(Sale.sale_date) >= start)
        .where(Sale.discount_amount > 0)
    ).scalars().all()

    total_discount = sum(float(s.discount_amount or 0) for s in sales)
    total_revenue = sum(float(s.total_amount) for s in sales)
    discount_count = len(sales)

    # Group by staff
    staff_discounts = {}
    for s in sales:
        uid = s.user_id
        if uid not in staff_discounts:
            staff_discounts[uid] = {"user_id": uid, "username": s.user.username if s.user else "Unknown",
                                     "total_discount": 0, "count": 0}
        staff_discounts[uid]["total_discount"] += float(s.discount_amount or 0)
        staff_discounts[uid]["count"] += 1

    staff_list = sorted(staff_discounts.values(), key=lambda x: x["total_discount"], reverse=True)

    return {
        "period_days": days,
        "total_discount_given": round(total_discount, 2),
        "discount_transactions": discount_count,
        "avg_discount_per_sale": round(total_discount / discount_count, 2) if discount_count else 0,
        "discount_as_pct_of_revenue": round((total_discount / total_revenue * 100), 2) if total_revenue else 0,
        "by_staff": staff_list,
        "insight": (
            f"NPR {total_discount:,.0f} lost to discounts in {days} days "
            f"({total_discount/total_revenue*100:.1f}% of revenue)."
        ) if total_revenue else "No discount data.",
    }


def inventory_shrinkage_analysis() -> dict:
    """Detect inventory shrinkage — stock reduced without sales or purchases."""
    products = db.session.execute(db.select(Product)).scalars().all()
    shrinkage_items = []

    for p in products:
        # Total purchased
        total_purchased = db.session.execute(
            db.select(func.coalesce(func.sum(StockMovement.change_amount), 0))
            .where(StockMovement.product_id == p.id)
            .where(StockMovement.change_amount > 0)
        ).scalar() or 0

        # Total sold (from stock movements)
        total_sold_movements = db.session.execute(
            db.select(func.coalesce(func.sum(StockMovement.change_amount), 0))
            .where(StockMovement.product_id == p.id)
            .where(StockMovement.change_type == "sale")
        ).scalar() or 0

        # Manual adjustments out
        manual_out = db.session.execute(
            db.select(func.coalesce(func.sum(StockMovement.change_amount), 0))
            .where(StockMovement.product_id == p.id)
            .where(StockMovement.change_type == "adjustment_out")
        ).scalar() or 0

        # Expected stock = purchased + initial - sold - manual_out
        # Shrinkage = expected - actual
        expected = int(total_purchased) + abs(int(total_sold_movements)) + abs(int(manual_out))
        # Simplified: if manual_out is large relative to sales, flag it
        if abs(int(manual_out)) > 0:
            shrinkage_value = abs(int(manual_out)) * float(p.cost_price)
            shrinkage_items.append({
                "id": p.id,
                "name": p.name,
                "sku": p.sku,
                "manual_adjustments_out": abs(int(manual_out)),
                "shrinkage_value": round(shrinkage_value, 2),
                "current_stock": p.quantity,
            })

    shrinkage_items.sort(key=lambda x: x["shrinkage_value"], reverse=True)
    total_shrinkage = sum(s["shrinkage_value"] for s in shrinkage_items)

    return {
        "shrinkage_items": shrinkage_items,
        "total_shrinkage_value": round(total_shrinkage, 2),
        "total_products_affected": len(shrinkage_items),
        "insight": (
            f"NPR {total_shrinkage:,.0f} in potential inventory shrinkage detected."
        ) if shrinkage_items else "No significant shrinkage detected.",
    }


def detect_fraud_signals(days: int = 30) -> dict:
    """Detect suspicious discount, return, and stock-adjustment behavior."""
    start = date.today() - timedelta(days=days)

    sales_rows = db.session.execute(
        db.select(
            Sale.user_id.label("user_id"),
            User.username.label("username"),
            func.count(Sale.id).label("sales_count"),
            func.coalesce(func.sum(Sale.total_amount), 0).label("gross_sales"),
            func.coalesce(func.sum(Sale.discount_amount), 0).label("discount_total"),
        )
        .join(User, User.id == Sale.user_id)
        .where(func.date(Sale.sale_date) >= start)
        .group_by(Sale.user_id, User.username)
    ).all()

    ratios = []
    discount_abuse = []
    for row in sales_rows:
        gross_sales = float(row.gross_sales or 0)
        discount_total = float(row.discount_total or 0)
        ratio = (discount_total / gross_sales * 100) if gross_sales > 0 else 0.0
        ratios.append(ratio)
        if row.sales_count >= 8 and ratio >= 8.0:
            discount_abuse.append(
                {
                    "user_id": row.user_id,
                    "username": row.username or "Unknown",
                    "sales_count": int(row.sales_count),
                    "discount_total": round(discount_total, 2),
                    "discount_ratio_pct": round(ratio, 2),
                    "risk_level": "high" if ratio >= 12 else "medium",
                }
            )
    avg_ratio = sum(ratios) / len(ratios) if ratios else 0

    return_rows = db.session.execute(
        db.select(
            SaleReturn.processed_by.label("user_id"),
            User.username.label("username"),
            func.count(SaleReturn.id).label("return_count"),
            func.coalesce(func.sum(SaleReturn.refund_amount), 0).label("refund_total"),
        )
        .join(User, User.id == SaleReturn.processed_by)
        .where(func.date(SaleReturn.created_at) >= start)
        .group_by(SaleReturn.processed_by, User.username)
    ).all()
    suspicious_returns = []
    for row in return_rows:
        refund_total = float(row.refund_total or 0)
        if row.return_count >= 4 and refund_total >= 3000:
            suspicious_returns.append(
                {
                    "user_id": row.user_id,
                    "username": row.username or "Unknown",
                    "return_count": int(row.return_count),
                    "refund_total": round(refund_total, 2),
                    "avg_refund": round(refund_total / max(1, int(row.return_count)), 2),
                    "risk_level": "high" if refund_total >= 10000 else "medium",
                }
            )

    adjustment_rows = db.session.execute(
        db.select(
            StockMovement.created_by.label("user_id"),
            User.username.label("username"),
            func.count(StockMovement.id).label("adjustment_events"),
            func.coalesce(func.sum(func.abs(StockMovement.change_amount)), 0).label("adjustment_qty"),
        )
        .join(User, User.id == StockMovement.created_by)
        .where(func.date(StockMovement.timestamp) >= start)
        .where(StockMovement.change_type == "adjustment_out")
        .group_by(StockMovement.created_by, User.username)
    ).all()
    suspicious_adjustments = []
    for row in adjustment_rows:
        qty = int(row.adjustment_qty or 0)
        events = int(row.adjustment_events or 0)
        if qty >= 20 or events >= 8:
            suspicious_adjustments.append(
                {
                    "user_id": row.user_id,
                    "username": row.username or "Unknown",
                    "adjustment_events": events,
                    "adjustment_qty": qty,
                    "risk_level": "high" if qty >= 50 else "medium",
                }
            )

    risk_score = min(
        100,
        len(discount_abuse) * 18 + len(suspicious_returns) * 28 + len(suspicious_adjustments) * 22,
    )
    return {
        "period_days": days,
        "discount_baseline_pct": round(avg_ratio, 2),
        "suspicious_discount_patterns": discount_abuse,
        "suspicious_return_patterns": suspicious_returns,
        "suspicious_stock_adjustments": suspicious_adjustments,
        "overall_risk_score": risk_score,
        "insight": (
            f"Detected {len(discount_abuse) + len(suspicious_returns) + len(suspicious_adjustments)} "
            "potential leak/fraud signal groups."
        ) if risk_score > 0 else "No significant fraud/leak signals detected in the selected period.",
    }


def profit_leak_dashboard() -> dict:
    """Combined profit leak analysis dashboard."""
    return {
        "low_margin": low_margin_products(15.0),
        "discount_losses": discount_loss_analysis(30),
        "shrinkage": inventory_shrinkage_analysis(),
        "fraud_signals": detect_fraud_signals(30),
        "generated_at": str(date.today()),
    }
