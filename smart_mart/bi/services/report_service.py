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

        # FIX 1: join Product so we get name + sku in one query (no N+1)
        item_stmt = (
            db.select(
                SaleItem.product_id.label("product_id"),
                Product.name.label("product_name"),
                Product.sku.label("sku"),
                func.coalesce(func.sum(SaleItem.subtotal), 0).label("revenue"),
                func.coalesce(func.sum(SaleItem.quantity * SaleItem.cost_price), 0).label("cogs"),
            )
            .join(sale_stmt, sale_stmt.c.id == SaleItem.sale_id)
            .join(Product, Product.id == SaleItem.product_id)
        )

        if start:
            item_stmt = item_stmt.where(func.date(sale_stmt.c.sale_date) >= start)
        if end:
            item_stmt = item_stmt.where(func.date(sale_stmt.c.sale_date) <= end)

        item_stmt = item_stmt.group_by(SaleItem.product_id, Product.name, Product.sku)
        rows = db.session.execute(item_stmt).all()

        total_sales = sum((as_decimal(r.revenue) for r in rows), Decimal("0"))
        total_cogs = sum((as_decimal(r.cogs) for r in rows), Decimal("0"))

        opex_stmt = db.select(func.coalesce(func.sum(OperatingExpense.amount), 0))
        if start:
            opex_stmt = opex_stmt.where(OperatingExpense.expense_date >= start)
        if end:
            opex_stmt = opex_stmt.where(OperatingExpense.expense_date <= end)
        total_opex = as_decimal(db.session.execute(opex_stmt).scalar() or 0)

        products = []
        allocations = ReportService._allocate_opex_by_revenue(total_opex, rows, total_sales)
        for row in rows:
            revenue = as_decimal(row.revenue)
            cogs = as_decimal(row.cogs)
            gross_profit = money(revenue - cogs)
            allocated_opex = allocations.get(row.product_id, Decimal("0.00"))
            net_profit = money(gross_profit - allocated_opex)
            # FIX 7: include gross_margin_pct
            gross_margin_pct = round(float(gross_profit / revenue * 100), 2) if revenue > 0 else 0.0
            products.append(
                {
                    "product_id": row.product_id,
                    "product_name": row.product_name,   # FIX 1
                    "sku": row.sku,                      # FIX 1
                    "revenue": decimal_to_float(money(revenue)),
                    "cogs": decimal_to_float(money(cogs)),
                    "gross_profit": decimal_to_float(gross_profit),
                    "gross_margin_pct": gross_margin_pct,  # FIX 7
                    "allocated_opex": decimal_to_float(allocated_opex),
                    "net_profit": decimal_to_float(net_profit),
                }
            )

        gross_profit = money(total_sales - total_cogs)
        net_profit = money(gross_profit - total_opex)
        overall_margin = round(float(gross_profit / total_sales * 100), 2) if total_sales > 0 else 0.0
        return {
            "overall": {
                "sales": decimal_to_float(money(total_sales)),
                "cogs": decimal_to_float(money(total_cogs)),
                "gross_profit": decimal_to_float(gross_profit),
                "gross_margin_pct": overall_margin,  # FIX 7
                "opex": decimal_to_float(money(total_opex)),
                "net_profit": decimal_to_float(net_profit),
            },
            "products": products,
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
        if total_opex <= 0 or total_revenue <= 0:
            return {row.product_id: Decimal("0.00") for row in rows}

        allocations = {}
        running = Decimal("0")
        for row in rows[:-1]:
            ratio = as_decimal(row.revenue) / total_revenue
            amount = money(total_opex * ratio)
            allocations[row.product_id] = amount
            running += amount

        if rows:
            last_id = rows[-1].product_id
            allocations[last_id] = money(total_opex - running)

        return allocations

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
