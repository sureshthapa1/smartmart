"""AI growth operations: auto replenishment and price optimization."""

from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import median

from sqlalchemy import func

from ..extensions import db
from ..models.ai_enhancements import CompetitorPriceEntry
from ..models.product import Product
from ..models.purchase import Purchase
from ..models.sale import Sale, SaleItem
from . import po_manager


def _estimate_supplier_lead_time(supplier_id: int | None) -> int:
    """Estimate lead time in days using historical purchase cadence."""
    if not supplier_id:
        return 5
    purchase_dates = db.session.execute(
        db.select(Purchase.purchase_date)
        .where(Purchase.supplier_id == supplier_id)
        .order_by(Purchase.purchase_date.desc())
        .limit(8)
    ).scalars().all()
    if len(purchase_dates) < 2:
        return 5
    gaps = []
    for i in range(len(purchase_dates) - 1):
        gap = (purchase_dates[i] - purchase_dates[i + 1]).days
        if gap > 0:
            gaps.append(gap)
    if not gaps:
        return 5
    return max(2, min(21, int(round(median(gaps)))))


def _avg_daily_demand(product_id: int, lookback_days: int) -> float:
    start = date.today() - timedelta(days=max(1, lookback_days - 1))
    qty = db.session.execute(
        db.select(func.coalesce(func.sum(SaleItem.quantity), 0))
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(SaleItem.product_id == product_id)
        .where(func.date(Sale.sale_date) >= start)
    ).scalar() or 0
    return float(qty) / max(1, lookback_days)


def auto_replenishment_plan(
    lookback_days: int = 30,
    safety_days: int = 4,
    coverage_days: int = 14,
) -> dict:
    """Create supplier-grouped replenishment recommendations."""
    products = db.session.execute(
        db.select(Product).order_by(Product.supplier_id, Product.name)
    ).scalars().all()
    groups: dict[int, dict] = {}
    unassigned = []

    for product in products:
        avg_daily = _avg_daily_demand(product.id, lookback_days)
        if avg_daily <= 0:
            continue
        lead_time_days = _estimate_supplier_lead_time(product.supplier_id)
        reorder_point = math.ceil(avg_daily * (lead_time_days + safety_days))
        target_stock = math.ceil(avg_daily * (lead_time_days + safety_days + coverage_days))
        if product.quantity > reorder_point:
            continue

        moq = max(1, int(product.reorder_point or 1))
        recommended_qty = max(moq, target_stock - int(product.quantity))
        days_of_stock_left = round(product.quantity / avg_daily, 1) if avg_daily > 0 else 999
        urgency = "critical" if days_of_stock_left <= lead_time_days else (
            "soon" if days_of_stock_left <= (lead_time_days + safety_days) else "ok"
        )
        item = {
            "product_id": product.id,
            "product_name": product.name,
            "sku": product.sku,
            "current_stock": int(product.quantity),
            "avg_daily_demand": round(avg_daily, 2),
            "lead_time_days": lead_time_days,
            "reorder_point": reorder_point,
            "target_stock": target_stock,
            "moq": moq,
            "recommended_qty": int(recommended_qty),
            "days_of_stock_left": days_of_stock_left,
            "urgency": urgency,
            "unit_cost": float(product.cost_price),
            "estimated_cost": round(float(product.cost_price) * recommended_qty, 2),
        }

        if not product.supplier_id:
            unassigned.append(item)
            continue
        supplier_key = int(product.supplier_id)
        if supplier_key not in groups:
            supplier_name = product.supplier.name if product.supplier else f"Supplier #{supplier_key}"
            groups[supplier_key] = {
                "supplier_id": supplier_key,
                "supplier_name": supplier_name,
                "items": [],
                "total_estimated_cost": 0.0,
            }
        groups[supplier_key]["items"].append(item)
        groups[supplier_key]["total_estimated_cost"] += item["estimated_cost"]

    supplier_groups = sorted(
        groups.values(),
        key=lambda g: (-len(g["items"]), -g["total_estimated_cost"], g["supplier_name"].lower()),
    )
    for grp in supplier_groups:
        grp["items"] = sorted(grp["items"], key=lambda x: (x["urgency"], -x["estimated_cost"]))
        grp["total_estimated_cost"] = round(grp["total_estimated_cost"], 2)

    return {
        "lookback_days": lookback_days,
        "safety_days": safety_days,
        "coverage_days": coverage_days,
        "supplier_groups": supplier_groups,
        "unassigned_products": unassigned,
        "total_supplier_orders": len(supplier_groups),
        "total_products_to_restock": sum(len(g["items"]) for g in supplier_groups) + len(unassigned),
        "total_estimated_cost": round(sum(g["total_estimated_cost"] for g in supplier_groups), 2),
    }


def create_auto_draft_purchase_orders(
    user_id: int,
    lookback_days: int = 30,
    safety_days: int = 4,
    coverage_days: int = 14,
) -> dict:
    """Create draft POs from the current replenishment plan."""
    plan = auto_replenishment_plan(
        lookback_days=lookback_days,
        safety_days=safety_days,
        coverage_days=coverage_days,
    )
    created_orders = []
    for grp in plan["supplier_groups"]:
        if not grp["items"]:
            continue
        lead_time = max(int(item["lead_time_days"]) for item in grp["items"])
        po = po_manager.create_po(
            supplier_id=int(grp["supplier_id"]),
            items=[
                {
                    "product_id": int(item["product_id"]),
                    "quantity": int(item["recommended_qty"]),
                    "unit_cost": float(item["unit_cost"]),
                }
                for item in grp["items"]
            ],
            user_id=user_id,
            expected_date=date.today() + timedelta(days=lead_time),
            notes=(
                "Auto-generated by AI replenishment planner "
                f"(lookback={lookback_days}d, safety={safety_days}d, coverage={coverage_days}d)."
            ),
        )
        created_orders.append(
            {
                "po_id": po.id,
                "po_number": po.po_number,
                "supplier_id": po.supplier_id,
                "supplier_name": grp["supplier_name"],
                "items_count": len(grp["items"]),
                "estimated_total": round(grp["total_estimated_cost"], 2),
            }
        )
    return {
        "created_order_count": len(created_orders),
        "orders": created_orders,
        "skipped_unassigned_products": len(plan["unassigned_products"]),
        "plan_summary": {
            "total_products_to_restock": plan["total_products_to_restock"],
            "total_estimated_cost": plan["total_estimated_cost"],
        },
    }


def optimize_product_prices(
    lookback_days: int = 45,
    max_adjustment_pct: float = 10.0,
    min_margin_pct: float = 8.0,
) -> dict:
    """Generate price recommendations with demand, margin, competitor, and expiry signals."""
    start = date.today() - timedelta(days=max(1, lookback_days - 1))
    competitor_cutoff = date.today() - timedelta(days=30)
    products = db.session.execute(db.select(Product).order_by(Product.name)).scalars().all()
    suggestions = []

    for product in products:
        current_price = float(product.selling_price)
        cost_price = float(product.cost_price)
        if current_price <= 0:
            continue

        qty_total = db.session.execute(
            db.select(func.coalesce(func.sum(SaleItem.quantity), 0))
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(SaleItem.product_id == product.id)
            .where(func.date(Sale.sale_date) >= start)
        ).scalar() or 0

        recent_start = date.today() - timedelta(days=13)
        prev_start = date.today() - timedelta(days=27)
        prev_end = date.today() - timedelta(days=14)

        recent_qty = db.session.execute(
            db.select(func.coalesce(func.sum(SaleItem.quantity), 0))
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(SaleItem.product_id == product.id)
            .where(func.date(Sale.sale_date) >= recent_start)
        ).scalar() or 0
        prev_qty = db.session.execute(
            db.select(func.coalesce(func.sum(SaleItem.quantity), 0))
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(SaleItem.product_id == product.id)
            .where(func.date(Sale.sale_date) >= prev_start)
            .where(func.date(Sale.sale_date) <= prev_end)
        ).scalar() or 0

        competitor_median = db.session.execute(
            db.select(func.avg(CompetitorPriceEntry.competitor_price))
            .where(CompetitorPriceEntry.product_id == product.id)
            .where(func.date(CompetitorPriceEntry.observed_at) >= competitor_cutoff)
        ).scalar()
        competitor_price = float(competitor_median) if competitor_median else None

        avg_daily = float(qty_total) / max(1, lookback_days)
        trend_ratio = ((float(recent_qty) - float(prev_qty)) / max(1.0, float(prev_qty)))
        days_to_expiry = None
        if product.expiry_date:
            days_to_expiry = (product.expiry_date - date.today()).days

        target_price = current_price
        reasons = []

        if days_to_expiry is not None and days_to_expiry <= 14 and product.quantity > max(5, int(avg_daily * 10)):
            target_price *= 0.92
            reasons.append("expiry risk with high stock")
        if trend_ratio > 0.20 and product.quantity < max(5, int(avg_daily * 7)):
            target_price *= 1.04
            reasons.append("strong demand and low stock")
        elif trend_ratio < -0.20 and product.quantity > max(10, int(avg_daily * 20)):
            target_price *= 0.95
            reasons.append("demand slowdown with overstock")

        if competitor_price is not None:
            if target_price > competitor_price * 1.08:
                target_price = competitor_price * 1.03
                reasons.append("aligning closer to competitor")
            elif target_price < competitor_price * 0.88 and trend_ratio >= 0:
                target_price = competitor_price * 0.96
                reasons.append("capturing margin while still competitive")

        min_safe_price = cost_price * (1 + max(0.0, min_margin_pct) / 100)
        target_price = max(min_safe_price, target_price)

        max_delta = current_price * (max_adjustment_pct / 100)
        target_price = max(current_price - max_delta, min(current_price + max_delta, target_price))
        target_price = round(target_price, 2)
        price_change_pct = ((target_price - current_price) / current_price) * 100

        base_daily_qty = max(0.05, avg_daily)
        elasticity = 1.15
        adjusted_daily_qty = max(0.05, base_daily_qty * (1 - elasticity * (price_change_pct / 100)))
        horizon_days = 30
        current_profit = (current_price - cost_price) * base_daily_qty * horizon_days
        projected_profit = (target_price - cost_price) * adjusted_daily_qty * horizon_days
        profit_impact = round(projected_profit - current_profit, 2)

        if abs(price_change_pct) < 0.5:
            action = "keep"
        elif price_change_pct > 0:
            action = "increase"
        else:
            action = "decrease"

        confidence = 0.55
        if competitor_price is not None:
            confidence += 0.15
        if qty_total >= 25:
            confidence += 0.20
        confidence = round(min(0.95, confidence), 2)

        suggestions.append(
            {
                "product_id": product.id,
                "product_name": product.name,
                "sku": product.sku,
                "action": action,
                "current_price": round(current_price, 2),
                "suggested_price": target_price,
                "price_change_pct": round(price_change_pct, 2),
                "current_margin_pct": round(((current_price - cost_price) / current_price) * 100, 2),
                "suggested_margin_pct": round(((target_price - cost_price) / target_price) * 100, 2),
                "competitor_price": round(competitor_price, 2) if competitor_price is not None else None,
                "demand_trend_14d_pct": round(trend_ratio * 100, 1),
                "days_to_expiry": days_to_expiry,
                "estimated_30d_profit_impact": profit_impact,
                "confidence": confidence,
                "reasons": reasons or ["price is already near optimal"],
            }
        )

    actionable = [s for s in suggestions if s["action"] != "keep"]
    actionable.sort(
        key=lambda s: (abs(s["estimated_30d_profit_impact"]), abs(s["price_change_pct"])),
        reverse=True,
    )
    return {
        "lookback_days": lookback_days,
        "max_adjustment_pct": max_adjustment_pct,
        "min_margin_pct": min_margin_pct,
        "total_products_analyzed": len(suggestions),
        "actionable_count": len(actionable),
        "estimated_total_30d_profit_impact": round(sum(s["estimated_30d_profit_impact"] for s in actionable), 2),
        "suggestions": actionable,
    }
