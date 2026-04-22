from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from flask import Response
from sqlalchemy import func

from ...extensions import db
from ...models.product import Product
from ...models.sale import Sale, SaleItem
from ..models.operating_expense import OperatingExpense
from ..utils import as_decimal, decimal_to_float, money


class ReportService:
    @staticmethod
    def profit_and_loss(start: date | None = None, end: date | None = None) -> dict:
        sale_stmt = db.select(Sale.id, Sale.sale_date).subquery()

        item_stmt = (
            db.select(
                SaleItem.product_id.label("product_id"),
                Product.name.label("product_name"),
                Product.sku.label("sku"),
                Product.cost_price.label("current_cost"),
                Product.selling_price.label("current_selling_price"),
                func.coalesce(func.sum(SaleItem.subtotal), 0).label("revenue"),
                func.coalesce(func.sum(SaleItem.quantity * SaleItem.cost_price), 0).label("cogs"),
                func.coalesce(func.sum(SaleItem.quantity), 0).label("qty_sold"),
            )
            .join(sale_stmt, sale_stmt.c.id == SaleItem.sale_id)
            .join(Product, Product.id == SaleItem.product_id)
        )

        if start:
            item_stmt = item_stmt.where(func.date(sale_stmt.c.sale_date) >= start)
        if end:
            item_stmt = item_stmt.where(func.date(sale_stmt.c.sale_date) <= end)

        item_stmt = item_stmt.group_by(
            SaleItem.product_id, Product.name, Product.sku,
            Product.cost_price, Product.selling_price,
        )
        rows = db.session.execute(item_stmt).all()

        total_sales = sum((as_decimal(r.revenue) for r in rows), Decimal("0"))
        total_cogs = sum((as_decimal(r.cogs) for r in rows), Decimal("0"))

        opex_stmt = db.select(func.coalesce(func.sum(OperatingExpense.amount), 0))
        if start:
            opex_stmt = opex_stmt.where(OperatingExpense.expense_date >= start)
        if end:
            opex_stmt = opex_stmt.where(OperatingExpense.expense_date <= end)
        # Exclude purchase-type expenses — their cost is already in COGS via SaleItem.cost_price
        opex_stmt = opex_stmt.where(OperatingExpense.category != "purchase")
        total_opex = as_decimal(db.session.execute(opex_stmt).scalar() or 0)

        products = []
        allocations = ReportService._allocate_opex_by_revenue(total_opex, rows, total_sales)
        for row in rows:
            revenue = as_decimal(row.revenue)
            cogs = as_decimal(row.cogs)
            qty_sold = int(row.qty_sold or 0)
            gross_profit = money(revenue - cogs)
            allocated_opex = allocations.get(row.product_id, Decimal("0.00"))
            net_profit = money(gross_profit - allocated_opex)
            gross_margin_pct = round(float(gross_profit / revenue * 100), 2) if revenue > 0 else 0.0
            net_margin_pct = round(float(net_profit / revenue * 100), 2) if revenue > 0 else 0.0

            # Break-even price: minimum price to cover COGS + allocated OPEX
            # break_even = (cogs + allocated_opex) / qty_sold
            current_cost = as_decimal(row.current_cost or 0)
            if qty_sold > 0:
                break_even_price = decimal_to_float(
                    money((cogs + allocated_opex) / Decimal(str(qty_sold)))
                )
            else:
                # No sales yet — use current cost as floor
                break_even_price = decimal_to_float(current_cost)

            products.append(
                {
                    "product_id": row.product_id,
                    "product_name": row.product_name,
                    "sku": row.sku,
                    "cost": decimal_to_float(current_cost),
                    "selling_price": decimal_to_float(as_decimal(row.current_selling_price or 0)),
                    "qty_sold": qty_sold,
                    "revenue": decimal_to_float(money(revenue)),
                    "cogs": decimal_to_float(money(cogs)),
                    "gross_profit": decimal_to_float(gross_profit),
                    "gross_margin_pct": gross_margin_pct,
                    "allocated_opex": decimal_to_float(allocated_opex),
                    "net_profit": decimal_to_float(net_profit),
                    "net_margin_pct": net_margin_pct,
                    "break_even_price": break_even_price,
                }
            )

        gross_profit = money(total_sales - total_cogs)
        net_profit = money(gross_profit - total_opex)
        overall_margin = round(float(gross_profit / total_sales * 100), 2) if total_sales > 0 else 0.0
        overall_net_margin = round(float(net_profit / total_sales * 100), 2) if total_sales > 0 else 0.0
        return {
            "overall": {
                "sales": decimal_to_float(money(total_sales)),
                "cogs": decimal_to_float(money(total_cogs)),
                "gross_profit": decimal_to_float(gross_profit),
                "gross_margin_pct": overall_margin,
                "opex": decimal_to_float(money(total_opex)),
                "net_profit": decimal_to_float(net_profit),
                "net_margin_pct": overall_net_margin,
            },
            "products": products,
        }

    # ── Profit Simulation (What-If Analysis) ─────────────────────────────────
    @staticmethod
    def simulate_profit(
        *,
        product_id: int,
        margin_pct: float | None = None,
        selling_price: float | None = None,
        expected_qty: int = 1,
        start: date | None = None,
        end: date | None = None,
    ) -> dict:
        """
        Simulate profit for a product given a margin % or selling price.
        Returns gross profit, net profit (after OPEX allocation), and break-even.
        """
        product = db.session.get(Product, product_id)
        if product is None:
            raise ValueError(f"Product {product_id} not found")

        final_cost = as_decimal(product.cost_price or 0)
        if final_cost <= 0:
            raise ValueError("Product has no cost price set")

        # Resolve selling price from margin or direct input
        if selling_price is not None:
            sim_price = as_decimal(selling_price)
            sim_margin = ((sim_price - final_cost) / sim_price * 100) if sim_price > 0 else Decimal("0")
        elif margin_pct is not None:
            sim_margin = as_decimal(margin_pct)
            sim_price = final_cost * (Decimal("1") + sim_margin / Decimal("100"))
        else:
            raise ValueError("Provide either margin_pct or selling_price")

        sim_price = money(sim_price)
        profit_per_unit = money(sim_price - final_cost)
        gross_profit = money(profit_per_unit * Decimal(str(expected_qty)))
        estimated_revenue = money(sim_price * Decimal(str(expected_qty)))
        estimated_cogs = money(final_cost * Decimal(str(expected_qty)))

        # Estimate OPEX allocation based on revenue share
        # Use total revenue from the period to compute share
        total_revenue_stmt = db.select(
            func.coalesce(func.sum(SaleItem.subtotal), 0)
        ).join(Sale, Sale.id == SaleItem.sale_id)
        if start:
            total_revenue_stmt = total_revenue_stmt.where(func.date(Sale.sale_date) >= start)
        if end:
            total_revenue_stmt = total_revenue_stmt.where(func.date(Sale.sale_date) <= end)
        total_revenue = as_decimal(db.session.execute(total_revenue_stmt).scalar() or 0)

        opex_stmt = db.select(func.coalesce(func.sum(OperatingExpense.amount), 0))
        if start:
            opex_stmt = opex_stmt.where(OperatingExpense.expense_date >= start)
        if end:
            opex_stmt = opex_stmt.where(OperatingExpense.expense_date <= end)
        opex_stmt = opex_stmt.where(OperatingExpense.category != "purchase")
        total_opex = as_decimal(db.session.execute(opex_stmt).scalar() or 0)

        # Allocate OPEX proportionally to simulated revenue
        combined_revenue = total_revenue + estimated_revenue
        if combined_revenue > 0 and total_opex > 0:
            allocated_opex = money(total_opex * (estimated_revenue / combined_revenue))
        else:
            allocated_opex = Decimal("0.00")

        net_profit = money(gross_profit - allocated_opex)
        net_margin_pct = round(float(net_profit / estimated_revenue * 100), 2) if estimated_revenue > 0 else 0.0

        # Break-even: minimum price to cover cost + allocated opex per unit
        if expected_qty > 0 and allocated_opex > 0:
            break_even = money(final_cost + (allocated_opex / Decimal(str(expected_qty))))
        else:
            break_even = money(final_cost)

        return {
            "product_id": product_id,
            "product_name": product.name,
            "sku": product.sku,
            "final_cost": decimal_to_float(final_cost),
            "margin_pct": decimal_to_float(money(sim_margin)),
            "selling_price": decimal_to_float(sim_price),
            "profit_per_unit": decimal_to_float(profit_per_unit),
            "expected_qty": expected_qty,
            "estimated_revenue": decimal_to_float(estimated_revenue),
            "estimated_cogs": decimal_to_float(estimated_cogs),
            "estimated_gross_profit": decimal_to_float(gross_profit),
            "estimated_allocated_opex": decimal_to_float(allocated_opex),
            "estimated_net_profit": decimal_to_float(net_profit),
            "net_margin_pct": net_margin_pct,
            "break_even_price": decimal_to_float(break_even),
            "above_break_even": sim_price >= break_even,
        }

    @staticmethod
    def dashboard_payload(start: date, end: date) -> dict:
        # FIX 3: compute P&L once, reuse for all dashboard sections
        pnl = ReportService.profit_and_loss(start, end)

        sales_rows = db.session.execute(
            db.select(
                func.date(Sale.sale_date).label("day"),
                func.coalesce(func.sum(Sale.total_amount), 0).label("sales"),
            )
            .where(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end)
            .group_by(func.date(Sale.sale_date))
            .order_by(func.date(Sale.sale_date))
        ).all()

        opex_rows = db.session.execute(
            db.select(
                OperatingExpense.category,
                func.coalesce(func.sum(OperatingExpense.amount), 0).label("amount"),
            )
            .where(OperatingExpense.expense_date >= start, OperatingExpense.expense_date <= end)
            .group_by(OperatingExpense.category)
            .order_by(func.coalesce(func.sum(OperatingExpense.amount), 0).desc())
        ).all()

        product_perf = db.session.execute(
            db.select(
                Product.name,
                func.coalesce(func.sum(SaleItem.subtotal), 0).label("revenue"),
                func.coalesce(func.sum(SaleItem.quantity * SaleItem.cost_price), 0).label("cogs"),
            )
            .join(Product, Product.id == SaleItem.product_id)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end)
            .group_by(Product.id, Product.name)
            .order_by(func.coalesce(func.sum(SaleItem.subtotal), 0).desc())
            .limit(10)
        ).all()

        # FIX 6: profit_trend = net profit per day (sales - cogs - daily opex share)
        # We use gross profit per day (sales - cogs); opex is period-level, not daily
        cogs_rows = db.session.execute(
            db.select(
                func.date(Sale.sale_date).label("day"),
                func.coalesce(func.sum(SaleItem.quantity * SaleItem.cost_price), 0).label("cogs"),
            )
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end)
            .group_by(func.date(Sale.sale_date))
            .order_by(func.date(Sale.sale_date))
        ).all()

        sales_map = {str(r.day): as_decimal(r.sales) for r in sales_rows}
        cogs_map = {str(r.day): as_decimal(r.cogs) for r in cogs_rows}

        # Spread total opex evenly across days for net profit trend
        total_days = max(1, (end - start).days + 1)
        total_opex = as_decimal(pnl["overall"]["opex"])
        daily_opex = money(total_opex / Decimal(str(total_days)))

        profit_trend_labels = []
        profit_trend_data = []
        current = start
        while current <= end:
            key = current.isoformat()
            day_sales = sales_map.get(key, Decimal("0"))
            day_cogs = cogs_map.get(key, Decimal("0"))
            day_net = money(day_sales - day_cogs - daily_opex)
            profit_trend_labels.append(key)
            profit_trend_data.append(decimal_to_float(day_net))
            current = date.fromordinal(current.toordinal() + 1)

        return {
            "kpis": pnl["overall"],
            "sales_trend": {
                "labels": [str(r.day) for r in sales_rows],
                "data": [decimal_to_float(money(r.sales)) for r in sales_rows],
            },
            # FIX 6: now shows net profit per day
            "profit_trend": {
                "labels": profit_trend_labels,
                "data": profit_trend_data,
            },
            "expense_breakdown": {
                "labels": [r.category for r in opex_rows],
                "data": [decimal_to_float(money(r.amount)) for r in opex_rows],
            },
            "product_performance": {
                "labels": [r.name for r in product_perf],
                "data": [decimal_to_float(money(as_decimal(r.revenue) - as_decimal(r.cogs))) for r in product_perf],
            },
            "products": pnl["products"],
        }

    @staticmethod
    def _allocate_opex_by_revenue(total_opex: Decimal, rows: list, total_revenue: Decimal) -> dict[int, Decimal]:
        if total_opex <= 0:
            return {row.product_id: Decimal("0.00") for row in rows}

        # Step 1: apply direct allocations (where product_id is set on the expense)
        direct_stmt = db.select(
            OperatingExpense.product_id,
            func.coalesce(func.sum(OperatingExpense.amount), 0).label("direct_amount"),
        ).where(
            OperatingExpense.product_id.isnot(None),
            OperatingExpense.category != "purchase",  # exclude purchase costs (already in COGS)
        ).group_by(OperatingExpense.product_id)
        direct_rows = db.session.execute(direct_stmt).all()
        direct_map: dict[int, Decimal] = {r.product_id: as_decimal(r.direct_amount) for r in direct_rows}
        total_direct = sum(direct_map.values(), Decimal("0"))

        remaining_opex = money(total_opex - total_direct)
        if remaining_opex < Decimal("0"):
            remaining_opex = Decimal("0")

        # Step 2: allocate remaining by revenue share
        allocations: dict[int, Decimal] = {}
        for pid in [row.product_id for row in rows]:
            allocations[pid] = direct_map.get(pid, Decimal("0.00"))

        if remaining_opex <= 0 or total_revenue <= 0:
            return allocations

        running = Decimal("0")
        for row in rows[:-1]:
            ratio = as_decimal(row.revenue) / total_revenue
            amount = money(remaining_opex * ratio)
            allocations[row.product_id] = allocations.get(row.product_id, Decimal("0")) + amount
            running += amount

        if rows:
            last_id = rows[-1].product_id
            allocations[last_id] = allocations.get(last_id, Decimal("0")) + money(remaining_opex - running)

        return allocations

    # ── Period comparison ─────────────────────────────────────────────────────
    @staticmethod
    def period_comparison(start: date, end: date) -> dict:
        from datetime import timedelta
        current = ReportService.profit_and_loss(start, end)
        delta = (end - start).days + 1
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=delta - 1)
        previous = ReportService.profit_and_loss(prev_start, prev_end)

        def pct_change(curr, prev):
            if prev == 0:
                return None
            return round((curr - prev) / abs(prev) * 100, 1)

        curr_o = current["overall"]
        prev_o = previous["overall"]
        return {
            "current": {"start": start.isoformat(), "end": end.isoformat(), **curr_o},
            "previous": {"start": prev_start.isoformat(), "end": prev_end.isoformat(), **prev_o},
            "changes": {
                "sales_pct": pct_change(curr_o["sales"], prev_o["sales"]),
                "gross_profit_pct": pct_change(curr_o["gross_profit"], prev_o["gross_profit"]),
                "net_profit_pct": pct_change(curr_o["net_profit"], prev_o["net_profit"]),
                "opex_pct": pct_change(curr_o["opex"], prev_o["opex"]),
            },
        }

    # ── Feature 1: Reorder Alert Report ──────────────────────────────────────
    @staticmethod
    def reorder_alerts() -> list[dict]:
        """Products where quantity <= reorder_point."""
        rows = db.session.execute(
            db.select(Product)
            .where(Product.quantity <= Product.reorder_point)
            .order_by(
                (Product.quantity - Product.reorder_point).asc(),
                Product.name.asc(),
            )
        ).scalars().all()
        return [
            {
                "product_id": p.id,
                "product_name": p.name,
                "sku": p.sku,
                "category": p.category,
                "quantity": int(p.quantity or 0),
                "reorder_point": int(p.reorder_point or 0),
                "shortage": max(0, int(p.reorder_point or 0) - int(p.quantity or 0)),
                "cost_price": decimal_to_float(p.cost_price or 0),
                "selling_price": decimal_to_float(p.selling_price or 0),
            }
            for p in rows
        ]

    # ── Feature 4: Category-level P&L ────────────────────────────────────────
    @staticmethod
    def profit_and_loss_by_category(
        start: date | None = None, end: date | None = None
    ) -> dict:
        sale_stmt = db.select(Sale.id, Sale.sale_date).subquery()
        item_stmt = (
            db.select(
                Product.category.label("category"),
                func.coalesce(func.sum(SaleItem.subtotal), 0).label("revenue"),
                func.coalesce(func.sum(SaleItem.quantity * SaleItem.cost_price), 0).label("cogs"),
                func.count(SaleItem.id.distinct()).label("txn_count"),
                func.coalesce(func.sum(SaleItem.quantity), 0).label("qty_sold"),
            )
            .join(sale_stmt, sale_stmt.c.id == SaleItem.sale_id)
            .join(Product, Product.id == SaleItem.product_id)
        )
        if start:
            item_stmt = item_stmt.where(func.date(sale_stmt.c.sale_date) >= start)
        if end:
            item_stmt = item_stmt.where(func.date(sale_stmt.c.sale_date) <= end)
        item_stmt = item_stmt.group_by(Product.category).order_by(
            func.coalesce(func.sum(SaleItem.subtotal), 0).desc()
        )
        rows = db.session.execute(item_stmt).all()

        total_sales = sum((as_decimal(r.revenue) for r in rows), Decimal("0"))
        total_cogs = sum((as_decimal(r.cogs) for r in rows), Decimal("0"))

        opex_stmt = db.select(func.coalesce(func.sum(OperatingExpense.amount), 0))
        if start:
            opex_stmt = opex_stmt.where(OperatingExpense.expense_date >= start)
        if end:
            opex_stmt = opex_stmt.where(OperatingExpense.expense_date <= end)
        opex_stmt = opex_stmt.where(OperatingExpense.category != "purchase")
        total_opex = as_decimal(db.session.execute(opex_stmt).scalar() or 0)

        categories = []
        running_opex = Decimal("0")
        for i, row in enumerate(rows):
            revenue = as_decimal(row.revenue)
            cogs = as_decimal(row.cogs)
            gross_profit = money(revenue - cogs)
            gross_margin_pct = round(float(gross_profit / revenue * 100), 2) if revenue > 0 else 0.0
            # Allocate opex by revenue share; last row gets remainder
            if i < len(rows) - 1:
                ratio = revenue / total_sales if total_sales > 0 else Decimal("0")
                allocated_opex = money(total_opex * ratio)
                running_opex += allocated_opex
            else:
                allocated_opex = money(total_opex - running_opex)
            net_profit = money(gross_profit - allocated_opex)
            categories.append({
                "category": row.category or "Uncategorized",
                "revenue": decimal_to_float(money(revenue)),
                "cogs": decimal_to_float(money(cogs)),
                "gross_profit": decimal_to_float(gross_profit),
                "gross_margin_pct": gross_margin_pct,
                "allocated_opex": decimal_to_float(allocated_opex),
                "net_profit": decimal_to_float(net_profit),
                "qty_sold": int(row.qty_sold),
                "txn_count": int(row.txn_count),
            })

        gross_profit = money(total_sales - total_cogs)
        net_profit = money(gross_profit - total_opex)
        overall_margin = round(float(gross_profit / total_sales * 100), 2) if total_sales > 0 else 0.0
        return {
            "overall": {
                "sales": decimal_to_float(money(total_sales)),
                "cogs": decimal_to_float(money(total_cogs)),
                "gross_profit": decimal_to_float(gross_profit),
                "gross_margin_pct": overall_margin,
                "opex": decimal_to_float(money(total_opex)),
                "net_profit": decimal_to_float(net_profit),
            },
            "categories": categories,
        }

    # ── Feature 10: Inventory Valuation Snapshot ──────────────────────────────
    @staticmethod
    def inventory_valuation_snapshot() -> dict:
        """Total stock value at cost, grouped by category."""
        rows = db.session.execute(
            db.select(
                Product.category.label("category"),
                func.count(Product.id).label("product_count"),
                func.coalesce(func.sum(Product.quantity), 0).label("total_qty"),
                func.coalesce(
                    func.sum(Product.quantity * Product.cost_price), 0
                ).label("total_value"),
            )
            .group_by(Product.category)
            .order_by(
                func.coalesce(func.sum(Product.quantity * Product.cost_price), 0).desc()
            )
        ).all()

        total_value = sum(as_decimal(r.total_value) for r in rows)
        total_qty = sum(int(r.total_qty) for r in rows)
        total_products = sum(int(r.product_count) for r in rows)

        categories = [
            {
                "category": r.category or "Uncategorized",
                "product_count": int(r.product_count),
                "total_qty": int(r.total_qty),
                "total_value": decimal_to_float(money(as_decimal(r.total_value))),
                "value_pct": round(
                    float(as_decimal(r.total_value) / total_value * 100), 2
                ) if total_value > 0 else 0.0,
            }
            for r in rows
        ]
        return {
            "summary": {
                "total_value": decimal_to_float(money(total_value)),
                "total_qty": total_qty,
                "total_products": total_products,
            },
            "categories": categories,
        }

    # ── Feature 7: Batch CSV export helper ───────────────────────────────────
    @staticmethod
    def batch_to_csv(batch) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Batch ID", "Supplier", "Date", "Status",
            "Allocation Method", "Subtotal", "Shared Expenses", "Grand Total",
        ])
        writer.writerow([
            batch.id, batch.supplier_name or "", batch.purchase_date.isoformat(),
            batch.status, batch.allocation_method,
            decimal_to_float(batch.subtotal_amount),
            decimal_to_float(batch.shared_expense_total),
            decimal_to_float(batch.grand_total),
        ])
        writer.writerow([])
        writer.writerow([
            "Item ID", "Product ID", "Product Name", "SKU",
            "Quantity", "Purchase Price", "Allocated Total",
            "Allocated Cost/Unit", "Final Cost",
        ])
        for item in batch.items:
            writer.writerow([
                item.id, item.product_id,
                item.product.name if item.product else "",
                item.product.sku if item.product else "",
                item.quantity,
                decimal_to_float(item.purchase_price),
                decimal_to_float(item.allocated_total),
                decimal_to_float(item.allocated_cost_per_unit),
                decimal_to_float(item.final_cost),
            ])
        writer.writerow([])
        writer.writerow(["Expense ID", "Type", "Amount"])
        for ex in batch.expenses:
            writer.writerow([ex.id, ex.expense_type, decimal_to_float(ex.amount)])
        return output.getvalue()
