"""
AnalyticsService — advanced BI analytics:
  15. Auto pricing for target profit
  16. AI margin recommendations
  17. Contribution margin
  18. Per-product inventory value
  19. Stock turnover ratio
  20. Safety stock alerts
  21. Profit tracking (daily/monthly trend)
  22. Product performance ranking
  23. Smart warnings
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func

from ...extensions import db
from ...models.product import Product
from ...models.sale import Sale, SaleItem
from ..models.operating_expense import OperatingExpense
from ..utils import as_decimal, decimal_to_float, money


class AnalyticsService:

    # ── 15. Auto Pricing for Target Profit ───────────────────────────────────
    @staticmethod
    def auto_price_for_target(
        target_profit: float,
        start: date | None = None,
        end: date | None = None,
    ) -> dict:
        """
        Given a target net profit, calculate the required selling price per product.
        Distributes the profit gap across products proportionally by revenue.

        new_selling_price = final_cost + allocated_opex_per_unit + extra_profit_per_unit
        """
        from .report_service import ReportService
        pnl = ReportService.profit_and_loss(start, end)
        current_net = as_decimal(pnl["overall"]["net_profit"])
        target = as_decimal(target_profit)
        profit_gap = money(target - current_net)
        total_revenue = as_decimal(pnl["overall"]["sales"])

        suggestions = []
        for p in pnl["products"]:
            revenue = as_decimal(p["revenue"])
            qty_sold = int(p.get("qty_sold") or 0)
            cost = as_decimal(p["cost"])
            allocated_opex = as_decimal(p["allocated_opex"])

            # Revenue share of the profit gap
            if total_revenue > 0 and qty_sold > 0:
                extra_profit_share = money(profit_gap * (revenue / total_revenue))
                extra_per_unit = money(extra_profit_share / Decimal(str(qty_sold)))
                opex_per_unit = money(allocated_opex / Decimal(str(qty_sold)))
                new_price = money(cost + opex_per_unit + extra_per_unit)
                required_margin = round(
                    float((new_price - cost) / new_price * 100), 2
                ) if new_price > 0 else 0.0
            else:
                extra_per_unit = Decimal("0")
                new_price = money(cost)
                required_margin = 0.0

            suggestions.append({
                "product_id": p["product_id"],
                "product_name": p["product_name"],
                "sku": p["sku"],
                "current_cost": decimal_to_float(cost),
                "current_selling_price": decimal_to_float(as_decimal(p["selling_price"])),
                "current_net_profit": decimal_to_float(as_decimal(p["net_profit"])),
                "suggested_selling_price": decimal_to_float(new_price),
                "required_margin_pct": required_margin,
                "extra_profit_per_unit": decimal_to_float(extra_per_unit),
            })

        return {
            "target_profit": decimal_to_float(target),
            "current_net_profit": decimal_to_float(current_net),
            "profit_gap": decimal_to_float(profit_gap),
            "suggestions": suggestions,
        }

    # ── 16. AI Margin Recommendations ────────────────────────────────────────
    @staticmethod
    def margin_recommendations(
        *,
        fast_sell_days: int = 7,
        fast_sell_qty: int = 10,
        slow_sell_days: int = 30,
        slow_sell_qty: int = 3,
        low_margin_threshold: float = 0.15,
        high_margin_threshold: float = 0.40,
    ) -> list[dict]:
        """
        Analyze each product's velocity, stock, and margin.
        Return recommended margin adjustments with reasons.
        """
        today = date.today()
        fast_cutoff = today - timedelta(days=fast_sell_days)
        slow_cutoff = today - timedelta(days=slow_sell_days)

        products = db.session.execute(db.select(Product)).scalars().all()

        # Recent sales velocity
        fast_rows = db.session.execute(
            db.select(
                SaleItem.product_id,
                func.coalesce(func.sum(SaleItem.quantity), 0).label("qty"),
            )
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(func.date(Sale.sale_date) >= fast_cutoff)
            .group_by(SaleItem.product_id)
        ).all()
        fast_map = {r.product_id: int(r.qty) for r in fast_rows}

        slow_rows = db.session.execute(
            db.select(
                SaleItem.product_id,
                func.coalesce(func.sum(SaleItem.quantity), 0).label("qty"),
            )
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(func.date(Sale.sale_date) >= slow_cutoff)
            .group_by(SaleItem.product_id)
        ).all()
        slow_map = {r.product_id: int(r.qty) for r in slow_rows}

        recommendations = []
        for product in products:
            cost = as_decimal(product.cost_price or 0)
            price = as_decimal(product.selling_price or 0)
            stock = int(product.quantity or 0)

            if price <= 0 or cost <= 0:
                continue

            margin = float((price - cost) / price)
            fast_qty = fast_map.get(product.id, 0)
            slow_qty = slow_map.get(product.id, 0)

            is_fast = fast_qty >= fast_sell_qty
            is_slow = slow_qty <= slow_sell_qty
            is_low_margin = margin < low_margin_threshold
            is_high_margin = margin > high_margin_threshold
            is_high_stock = stock > 50

            # Decision logic
            if is_fast and is_low_margin:
                recommended_margin = round(margin * 100 + 5, 1)
                action = "increase_margin"
                reason = f"Fast selling ({fast_qty} units/{fast_sell_days}d) but low margin ({round(margin*100,1)}%). Safe to increase price."
            elif is_fast and not is_low_margin:
                recommended_margin = round(margin * 100 + 2, 1)
                action = "slight_increase"
                reason = f"High demand ({fast_qty} units/{fast_sell_days}d). Small price increase possible."
            elif is_slow and is_high_stock:
                recommended_margin = max(5.0, round(margin * 100 - 8, 1))
                action = "decrease_margin"
                reason = f"Slow movement ({slow_qty} units/{slow_sell_days}d) with high stock ({stock} units). Reduce price to clear."
            elif is_slow and not is_high_stock:
                recommended_margin = max(5.0, round(margin * 100 - 3, 1))
                action = "slight_decrease"
                reason = f"Low sales velocity ({slow_qty} units/{slow_sell_days}d). Minor price reduction may help."
            elif is_high_margin and is_fast:
                recommended_margin = round(margin * 100, 1)
                action = "maintain"
                reason = f"High margin ({round(margin*100,1)}%) with good sales. Maintain current pricing."
            else:
                recommended_margin = round(margin * 100, 1)
                action = "maintain"
                reason = "Balanced margin and velocity. No change needed."

            recommendations.append({
                "product_id": product.id,
                "product_name": product.name,
                "sku": product.sku,
                "current_margin_pct": round(margin * 100, 2),
                "recommended_margin_pct": recommended_margin,
                "action": action,
                "reason": reason,
                "fast_qty_7d": fast_qty,
                "slow_qty_30d": slow_qty,
                "stock_qty": stock,
                "current_selling_price": decimal_to_float(price),
                "suggested_price": decimal_to_float(
                    money(cost / (Decimal("1") - Decimal(str(recommended_margin)) / Decimal("100")))
                ) if recommended_margin < 100 else decimal_to_float(price),
            })

        # Sort: products needing action first
        action_order = {"increase_margin": 0, "decrease_margin": 1, "slight_increase": 2, "slight_decrease": 3, "maintain": 4}
        recommendations.sort(key=lambda x: action_order.get(x["action"], 5))
        return recommendations

    # ── 17. Contribution Margin ───────────────────────────────────────────────
    @staticmethod
    def contribution_margin(
        start: date | None = None,
        end: date | None = None,
    ) -> dict:
        """
        contribution_per_unit = selling_price - variable_cost (COGS)
        contribution_margin_ratio = contribution_per_unit / selling_price
        Used for break-even and pricing decisions.
        """
        item_stmt = (
            db.select(
                SaleItem.product_id,
                Product.name.label("product_name"),
                Product.sku.label("sku"),
                Product.cost_price.label("cost"),
                Product.selling_price.label("selling_price"),
                func.coalesce(func.sum(SaleItem.subtotal), 0).label("revenue"),
                func.coalesce(func.sum(SaleItem.quantity * SaleItem.cost_price), 0).label("variable_cost"),
                func.coalesce(func.sum(SaleItem.quantity), 0).label("qty_sold"),
            )
            .join(Sale, Sale.id == SaleItem.sale_id)
            .join(Product, Product.id == SaleItem.product_id)
        )
        if start:
            item_stmt = item_stmt.where(func.date(Sale.sale_date) >= start)
        if end:
            item_stmt = item_stmt.where(func.date(Sale.sale_date) <= end)
        item_stmt = item_stmt.group_by(
            SaleItem.product_id, Product.name, Product.sku,
            Product.cost_price, Product.selling_price,
        )
        rows = db.session.execute(item_stmt).all()

        products = []
        total_revenue = Decimal("0")
        total_variable_cost = Decimal("0")

        for row in rows:
            revenue = as_decimal(row.revenue)
            var_cost = as_decimal(row.variable_cost)
            qty = int(row.qty_sold or 0)
            sell_price = as_decimal(row.selling_price or 0)
            cost = as_decimal(row.cost or 0)

            contribution = money(revenue - var_cost)
            contribution_per_unit = money(sell_price - cost)
            cm_ratio = round(float(contribution / revenue * 100), 2) if revenue > 0 else 0.0

            total_revenue += revenue
            total_variable_cost += var_cost

            products.append({
                "product_id": row.product_id,
                "product_name": row.product_name,
                "sku": row.sku,
                "selling_price": decimal_to_float(sell_price),
                "variable_cost_per_unit": decimal_to_float(cost),
                "contribution_per_unit": decimal_to_float(contribution_per_unit),
                "contribution_margin_ratio_pct": cm_ratio,
                "qty_sold": qty,
                "total_contribution": decimal_to_float(contribution),
                "total_revenue": decimal_to_float(money(revenue)),
            })

        total_contribution = money(total_revenue - total_variable_cost)
        overall_cm_ratio = round(
            float(total_contribution / total_revenue * 100), 2
        ) if total_revenue > 0 else 0.0

        return {
            "overall": {
                "total_revenue": decimal_to_float(money(total_revenue)),
                "total_variable_cost": decimal_to_float(money(total_variable_cost)),
                "total_contribution": decimal_to_float(total_contribution),
                "contribution_margin_ratio_pct": overall_cm_ratio,
            },
            "products": sorted(products, key=lambda x: x["total_contribution"], reverse=True),
        }

    # ── 18. Per-Product Inventory Value ──────────────────────────────────────
    @staticmethod
    def inventory_value_per_product() -> dict:
        """inventory_value = quantity * cost_price per product."""
        rows = db.session.execute(
            db.select(Product).order_by(
                (Product.quantity * Product.cost_price).desc()
            )
        ).scalars().all()

        total_value = sum(
            as_decimal(p.quantity or 0) * as_decimal(p.cost_price or 0)
            for p in rows
        )

        products = [
            {
                "product_id": p.id,
                "product_name": p.name,
                "sku": p.sku,
                "category": p.category,
                "quantity": int(p.quantity or 0),
                "cost_price": decimal_to_float(p.cost_price or 0),
                "selling_price": decimal_to_float(p.selling_price or 0),
                "inventory_value": decimal_to_float(
                    money(as_decimal(p.quantity or 0) * as_decimal(p.cost_price or 0))
                ),
                "value_pct": round(
                    float(
                        as_decimal(p.quantity or 0) * as_decimal(p.cost_price or 0)
                        / total_value * 100
                    ), 2
                ) if total_value > 0 else 0.0,
            }
            for p in rows
        ]
        return {
            "total_inventory_value": decimal_to_float(money(total_value)),
            "product_count": len(products),
            "products": products,
        }

    # ── 19. Stock Turnover ────────────────────────────────────────────────────
    @staticmethod
    def stock_turnover(
        start: date | None = None,
        end: date | None = None,
    ) -> dict:
        """
        turnover_ratio = total_cogs / average_inventory_value
        average_inventory = (opening + closing) / 2
        We approximate: average = current inventory value (no historical snapshots)
        """
        # COGS per product in period
        cogs_rows = db.session.execute(
            db.select(
                SaleItem.product_id,
                Product.name.label("product_name"),
                Product.sku.label("sku"),
                Product.category.label("category"),
                func.coalesce(func.sum(SaleItem.quantity * SaleItem.cost_price), 0).label("cogs"),
                func.coalesce(func.sum(SaleItem.quantity), 0).label("qty_sold"),
            )
            .join(Sale, Sale.id == SaleItem.sale_id)
            .join(Product, Product.id == SaleItem.product_id)
        )
        if start:
            cogs_rows = cogs_rows.where(func.date(Sale.sale_date) >= start)
        if end:
            cogs_rows = cogs_rows.where(func.date(Sale.sale_date) <= end)
        cogs_rows = cogs_rows.group_by(
            SaleItem.product_id, Product.name, Product.sku, Product.category
        )
        cogs_data = db.session.execute(cogs_rows).all()

        # Current inventory value per product
        inv_rows = db.session.execute(db.select(Product)).scalars().all()
        inv_map = {
            p.id: as_decimal(p.quantity or 0) * as_decimal(p.cost_price or 0)
            for p in inv_rows
        }

        products = []
        total_cogs = Decimal("0")
        total_inv_value = sum(inv_map.values(), Decimal("0"))

        for row in cogs_data:
            cogs = as_decimal(row.cogs)
            inv_value = inv_map.get(row.product_id, Decimal("0"))
            turnover = round(float(cogs / inv_value), 2) if inv_value > 0 else None
            qty_sold = int(row.qty_sold or 0)

            # Classify movement
            if turnover is None:
                movement = "no_inventory"
            elif turnover >= 4:
                movement = "fast"
            elif turnover >= 1:
                movement = "normal"
            elif turnover >= 0.1:
                movement = "slow"
            else:
                movement = "dead"

            total_cogs += cogs
            products.append({
                "product_id": row.product_id,
                "product_name": row.product_name,
                "sku": row.sku,
                "category": row.category,
                "cogs": decimal_to_float(money(cogs)),
                "inventory_value": decimal_to_float(money(inv_value)),
                "qty_sold": qty_sold,
                "turnover_ratio": turnover,
                "movement": movement,
            })

        overall_turnover = round(
            float(total_cogs / total_inv_value), 2
        ) if total_inv_value > 0 else None

        products.sort(key=lambda x: (x["turnover_ratio"] or 0), reverse=True)
        return {
            "overall_turnover_ratio": overall_turnover,
            "total_cogs": decimal_to_float(money(total_cogs)),
            "total_inventory_value": decimal_to_float(money(total_inv_value)),
            "products": products,
        }

    # ── 20. Safety Stock Alerts ───────────────────────────────────────────────
    @staticmethod
    def safety_stock_alerts(custom_threshold: int | None = None) -> list[dict]:
        """
        Alert when stock <= reorder_point (or custom_threshold if provided).
        Returns products needing restock, sorted by urgency.
        """
        stmt = db.select(Product).where(Product.quantity >= 0)
        products = db.session.execute(stmt).scalars().all()

        alerts = []
        for p in products:
            threshold = custom_threshold if custom_threshold is not None else int(p.reorder_point or 10)
            qty = int(p.quantity or 0)
            if qty <= threshold:
                urgency = "critical" if qty == 0 else ("high" if qty <= threshold // 2 else "medium")
                alerts.append({
                    "product_id": p.id,
                    "product_name": p.name,
                    "sku": p.sku,
                    "category": p.category,
                    "current_qty": qty,
                    "reorder_point": threshold,
                    "shortage": max(0, threshold - qty),
                    "urgency": urgency,
                    "cost_price": decimal_to_float(p.cost_price or 0),
                    "restock_cost_estimate": decimal_to_float(
                        money(as_decimal(p.cost_price or 0) * Decimal(str(max(0, threshold - qty))))
                    ),
                })

        alerts.sort(key=lambda x: ({"critical": 0, "high": 1, "medium": 2}.get(x["urgency"], 3), x["current_qty"]))
        return alerts

    # ── 21. Profit Tracking (daily + monthly) ────────────────────────────────
    @staticmethod
    def profit_tracking(days: int = 30) -> dict:
        """
        Daily and monthly profit trend for the last N days.
        net_profit_per_day = sales - cogs - daily_opex_share
        """
        today = date.today()
        start = today - timedelta(days=days - 1)

        sales_rows = db.session.execute(
            db.select(
                func.date(Sale.sale_date).label("day"),
                func.coalesce(func.sum(Sale.total_amount), 0).label("sales"),
            )
            .where(func.date(Sale.sale_date) >= start)
            .group_by(func.date(Sale.sale_date))
            .order_by(func.date(Sale.sale_date))
        ).all()

        cogs_rows = db.session.execute(
            db.select(
                func.date(Sale.sale_date).label("day"),
                func.coalesce(func.sum(SaleItem.quantity * SaleItem.cost_price), 0).label("cogs"),
            )
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(func.date(Sale.sale_date) >= start)
            .group_by(func.date(Sale.sale_date))
            .order_by(func.date(Sale.sale_date))
        ).all()

        opex_rows = db.session.execute(
            db.select(
                OperatingExpense.expense_date.label("day"),
                func.coalesce(func.sum(OperatingExpense.amount), 0).label("opex"),
            )
            .where(OperatingExpense.expense_date >= start)
            .group_by(OperatingExpense.expense_date)
            .order_by(OperatingExpense.expense_date)
        ).all()

        sales_map = {str(r.day): as_decimal(r.sales) for r in sales_rows}
        cogs_map = {str(r.day): as_decimal(r.cogs) for r in cogs_rows}
        opex_map = {str(r.day): as_decimal(r.opex) for r in opex_rows}

        daily = []
        current = start
        while current <= today:
            key = current.isoformat()
            day_sales = sales_map.get(key, Decimal("0"))
            day_cogs = cogs_map.get(key, Decimal("0"))
            day_opex = opex_map.get(key, Decimal("0"))
            gross = money(day_sales - day_cogs)
            net = money(gross - day_opex)
            daily.append({
                "date": key,
                "sales": decimal_to_float(money(day_sales)),
                "cogs": decimal_to_float(money(day_cogs)),
                "gross_profit": decimal_to_float(gross),
                "opex": decimal_to_float(money(day_opex)),
                "net_profit": decimal_to_float(net),
            })
            current = date.fromordinal(current.toordinal() + 1)

        # Monthly rollup
        monthly: dict[str, dict] = {}
        for d in daily:
            month_key = d["date"][:7]
            if month_key not in monthly:
                monthly[month_key] = {"month": month_key, "sales": 0.0, "cogs": 0.0, "gross_profit": 0.0, "opex": 0.0, "net_profit": 0.0}
            for k in ("sales", "cogs", "gross_profit", "opex", "net_profit"):
                monthly[month_key][k] = round(monthly[month_key][k] + d[k], 2)

        return {
            "daily": daily,
            "monthly": list(monthly.values()),
            "period_days": days,
        }

    # ── 22. Product Performance Analysis ─────────────────────────────────────
    @staticmethod
    def product_performance(
        start: date | None = None,
        end: date | None = None,
        top_n: int = 10,
    ) -> dict:
        """
        Rank products by net profit. Identify top, loss, and low-margin products.
        """
        from .report_service import ReportService
        pnl = ReportService.profit_and_loss(start, end)
        products = pnl["products"]

        sorted_by_profit = sorted(products, key=lambda x: x["net_profit"], reverse=True)
        top_profit = sorted_by_profit[:top_n]
        loss_products = [p for p in products if p["net_profit"] < 0]
        low_margin = [p for p in products if 0 <= p["net_margin_pct"] < 10]
        below_breakeven = [p for p in products if p["selling_price"] < p["break_even_price"]]

        return {
            "overall": pnl["overall"],
            "top_profit_products": top_profit,
            "loss_products": loss_products,
            "low_margin_products": low_margin,
            "below_breakeven_products": below_breakeven,
            "summary": {
                "total_products": len(products),
                "profitable": len([p for p in products if p["net_profit"] > 0]),
                "loss_making": len(loss_products),
                "low_margin": len(low_margin),
                "below_breakeven": len(below_breakeven),
            },
        }

    # ── 23. Smart Warnings ────────────────────────────────────────────────────
    @staticmethod
    def smart_warnings(
        start: date | None = None,
        end: date | None = None,
        low_margin_threshold: float = 0.10,
        dead_stock_days: int = 30,
        overstock_threshold: int = 100,
    ) -> list[dict]:
        """
        Comprehensive smart warnings:
        - selling below cost
        - low margin
        - dead stock
        - overstock
        - below break-even
        """
        from .report_service import ReportService
        pnl = ReportService.profit_and_loss(start, end)
        today = date.today()
        dead_cutoff = today - timedelta(days=dead_stock_days)

        # Last sale date per product
        last_sale_rows = db.session.execute(
            db.select(
                SaleItem.product_id,
                func.max(func.date(Sale.sale_date)).label("last_sale"),
            )
            .join(Sale, Sale.id == SaleItem.sale_id)
            .group_by(SaleItem.product_id)
        ).all()
        last_sale_map = {r.product_id: r.last_sale for r in last_sale_rows}

        warnings = []
        for p in pnl["products"]:
            pid = p["product_id"]
            cost = p["cost"]
            price = p["selling_price"]
            margin = (price - cost) / price if price > 0 else 0
            be = p["break_even_price"]

            # Selling below cost
            if price < cost and cost > 0:
                warnings.append({
                    "type": "selling_below_cost",
                    "severity": "critical",
                    "product_id": pid,
                    "product_name": p["product_name"],
                    "sku": p["sku"],
                    "message": f"'{p['product_name']}' is selling BELOW cost (price: {price:.2f} < cost: {cost:.2f})",
                    "action": "increase_price_immediately",
                    "data": {"cost": cost, "selling_price": price, "loss_per_unit": round(cost - price, 2)},
                })

            # Below break-even
            elif price < be:
                warnings.append({
                    "type": "below_breakeven",
                    "severity": "high",
                    "product_id": pid,
                    "product_name": p["product_name"],
                    "sku": p["sku"],
                    "message": f"'{p['product_name']}' price is below break-even (price: {price:.2f} < break-even: {be:.2f})",
                    "action": "increase_price",
                    "data": {"selling_price": price, "break_even_price": be, "gap": round(be - price, 2)},
                })

            # Low margin
            elif margin < low_margin_threshold and price > 0:
                warnings.append({
                    "type": "low_margin",
                    "severity": "medium",
                    "product_id": pid,
                    "product_name": p["product_name"],
                    "sku": p["sku"],
                    "message": f"'{p['product_name']}' has low margin ({round(margin*100,1)}%)",
                    "action": "review_pricing",
                    "data": {"current_margin_pct": round(margin * 100, 2), "threshold_pct": round(low_margin_threshold * 100, 2)},
                })

        # Dead stock and overstock from inventory
        all_products = db.session.execute(db.select(Product)).scalars().all()
        for product in all_products:
            qty = int(product.quantity or 0)
            last_sale = last_sale_map.get(product.id)
            if isinstance(last_sale, str):
                last_sale = date.fromisoformat(last_sale)

            if qty > 0 and (last_sale is None or last_sale < dead_cutoff):
                warnings.append({
                    "type": "dead_stock",
                    "severity": "medium",
                    "product_id": product.id,
                    "product_name": product.name,
                    "sku": product.sku,
                    "message": f"'{product.name}' has {qty} units with no recent sales",
                    "action": "discount_or_bundle",
                    "data": {"qty": qty, "last_sale_date": last_sale.isoformat() if last_sale else None},
                })

            if qty >= overstock_threshold:
                warnings.append({
                    "type": "overstock",
                    "severity": "low",
                    "product_id": product.id,
                    "product_name": product.name,
                    "sku": product.sku,
                    "message": f"'{product.name}' has high stock ({qty} units)",
                    "action": "run_promotion",
                    "data": {"qty": qty, "threshold": overstock_threshold},
                })

        # Sort by severity
        sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        warnings.sort(key=lambda x: sev_order.get(x["severity"], 4))
        return warnings
