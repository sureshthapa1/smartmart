from __future__ import annotations

from datetime import date
from decimal import Decimal

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
                func.coalesce(func.sum(SaleItem.subtotal), 0).label("revenue"),
                func.coalesce(func.sum(SaleItem.quantity * SaleItem.cost_price), 0).label("cogs"),
            )
            .join(sale_stmt, sale_stmt.c.id == SaleItem.sale_id)
        )

        if start:
            item_stmt = item_stmt.where(func.date(sale_stmt.c.sale_date) >= start)
        if end:
            item_stmt = item_stmt.where(func.date(sale_stmt.c.sale_date) <= end)

        item_stmt = item_stmt.group_by(SaleItem.product_id)
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
            products.append(
                {
                    "product_id": row.product_id,
                    "revenue": decimal_to_float(money(revenue)),
                    "cogs": decimal_to_float(money(cogs)),
                    "gross_profit": decimal_to_float(gross_profit),
                    "allocated_opex": decimal_to_float(allocated_opex),
                    "net_profit": decimal_to_float(net_profit),
                }
            )

        gross_profit = money(total_sales - total_cogs)
        net_profit = money(gross_profit - total_opex)
        return {
            "overall": {
                "sales": decimal_to_float(money(total_sales)),
                "cogs": decimal_to_float(money(total_cogs)),
                "gross_profit": decimal_to_float(gross_profit),
                "opex": decimal_to_float(money(total_opex)),
                "net_profit": decimal_to_float(net_profit),
            },
            "products": products,
        }

    @staticmethod
    def dashboard_payload(start: date, end: date) -> dict:
        pnl = ReportService.profit_and_loss(start, end)

        sales_rows = db.session.execute(
            db.select(func.date(Sale.sale_date).label("day"), func.coalesce(func.sum(Sale.total_amount), 0).label("sales"))
            .where(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end)
            .group_by(func.date(Sale.sale_date))
            .order_by(func.date(Sale.sale_date))
        ).all()

        opex_rows = db.session.execute(
            db.select(OperatingExpense.category, func.coalesce(func.sum(OperatingExpense.amount), 0).label("amount"))
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

        profit_by_day = []
        sales_map = {str(r.day): as_decimal(r.sales) for r in sales_rows}
        cogs_rows = db.session.execute(
            db.select(func.date(Sale.sale_date).label("day"), func.coalesce(func.sum(SaleItem.quantity * SaleItem.cost_price), 0).label("cogs"))
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end)
            .group_by(func.date(Sale.sale_date))
            .order_by(func.date(Sale.sale_date))
        ).all()
        cogs_map = {str(r.day): as_decimal(r.cogs) for r in cogs_rows}

        current = start
        while current <= end:
            key = current.isoformat()
            profit_by_day.append(decimal_to_float(money(sales_map.get(key, Decimal("0")) - cogs_map.get(key, Decimal("0")))) )
            current = date.fromordinal(current.toordinal() + 1)

        return {
            "kpis": pnl["overall"],
            "sales_trend": {
                "labels": [str(r.day) for r in sales_rows],
                "data": [decimal_to_float(money(r.sales)) for r in sales_rows],
            },
            "profit_trend": {
                "labels": [(date.fromordinal(start.toordinal() + i)).isoformat() for i in range((end - start).days + 1)],
                "data": profit_by_day,
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
