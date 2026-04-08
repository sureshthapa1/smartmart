"""AI Module 5: What-If Business Simulation Engine

Simulates business scenarios:
- Sales increase/decrease impact
- Price change impact
- Supplier delay impact
- Expense change impact
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func

from ..extensions import db
from ..models.product import Product
from ..models.sale import Sale, SaleItem
from ..models.expense import Expense
from ..models.purchase import Purchase


def _get_baseline(days: int = 30) -> dict:
    """Get current baseline metrics."""
    start = date.today() - timedelta(days=days)

    revenue = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= start)
    ).scalar() or 0

    cogs_rows = db.session.execute(
        db.select(Product.cost_price, func.sum(SaleItem.quantity).label("qty"))
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= start)
        .group_by(Product.id)
    ).all()
    cogs = sum(float(r.cost_price) * r.qty for r in cogs_rows)

    expenses = db.session.execute(
        db.select(func.coalesce(func.sum(Expense.amount), 0))
        .where(Expense.expense_date >= start)
    ).scalar() or 0

    transactions = db.session.execute(
        db.select(func.count(Sale.id))
        .where(func.date(Sale.sale_date) >= start)
    ).scalar() or 0

    return {
        "revenue": float(revenue),
        "cogs": cogs,
        "expenses": float(expenses),
        "gross_profit": float(revenue) - cogs,
        "net_profit": float(revenue) - cogs - float(expenses),
        "transactions": transactions,
        "avg_sale": float(revenue) / transactions if transactions else 0,
        "period_days": days,
    }


def simulate_sales_change(change_pct: float, days: int = 30) -> dict:
    """Simulate impact of sales volume increase/decrease.

    Args:
        change_pct: percentage change (-50 to +200)
        days: baseline period
    """
    baseline = _get_baseline(days)
    factor = 1 + (change_pct / 100)

    new_revenue = baseline["revenue"] * factor
    new_cogs = baseline["cogs"] * factor  # COGS scales with sales
    new_gross_profit = new_revenue - new_cogs
    new_net_profit = new_gross_profit - baseline["expenses"]

    return {
        "scenario": f"Sales {'increase' if change_pct > 0 else 'decrease'} by {abs(change_pct):.0f}%",
        "change_pct": change_pct,
        "baseline": baseline,
        "simulated": {
            "revenue": round(new_revenue, 2),
            "cogs": round(new_cogs, 2),
            "expenses": round(baseline["expenses"], 2),
            "gross_profit": round(new_gross_profit, 2),
            "net_profit": round(new_net_profit, 2),
            "transactions": round(baseline["transactions"] * factor),
        },
        "impact": {
            "revenue_change": round(new_revenue - baseline["revenue"], 2),
            "profit_change": round(new_net_profit - baseline["net_profit"], 2),
            "profit_change_pct": round(
                ((new_net_profit - baseline["net_profit"]) / abs(baseline["net_profit"]) * 100)
                if baseline["net_profit"] != 0 else 0, 1
            ),
        },
        "recommendation": _sales_change_recommendation(change_pct, new_net_profit),
    }


def simulate_price_change(product_id: int, new_price: float, days: int = 30) -> dict:
    """Simulate impact of changing a product's selling price."""
    product = db.session.get(Product, product_id)
    if not product:
        return {"error": "Product not found"}

    start = date.today() - timedelta(days=days)
    qty_sold = db.session.execute(
        db.select(func.coalesce(func.sum(SaleItem.quantity), 0))
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(SaleItem.product_id == product_id)
        .where(func.date(Sale.sale_date) >= start)
    ).scalar() or 0

    old_price = float(product.selling_price)
    cost = float(product.cost_price)

    old_revenue = old_price * qty_sold
    new_revenue = new_price * qty_sold
    old_profit = (old_price - cost) * qty_sold
    new_profit = (new_price - cost) * qty_sold
    old_margin = ((old_price - cost) / old_price * 100) if old_price > 0 else 0
    new_margin = ((new_price - cost) / new_price * 100) if new_price > 0 else 0

    return {
        "scenario": f"Price change for '{product.name}'",
        "product": {"id": product.id, "name": product.name, "sku": product.sku},
        "baseline": {
            "price": old_price,
            "margin_pct": round(old_margin, 2),
            "revenue": round(old_revenue, 2),
            "profit": round(old_profit, 2),
            "qty_sold": qty_sold,
        },
        "simulated": {
            "price": new_price,
            "margin_pct": round(new_margin, 2),
            "revenue": round(new_revenue, 2),
            "profit": round(new_profit, 2),
        },
        "impact": {
            "revenue_change": round(new_revenue - old_revenue, 2),
            "profit_change": round(new_profit - old_profit, 2),
            "margin_change": round(new_margin - old_margin, 2),
        },
        "warning": "Selling below cost price!" if new_price < cost else None,
        "recommendation": (
            f"At NPR {new_price:.2f}, margin is {new_margin:.1f}%. "
            f"{'Profitable.' if new_margin > 0 else 'LOSS-MAKING!'}"
        ),
    }


def simulate_expense_change(change_pct: float, days: int = 30) -> dict:
    """Simulate impact of expense increase/decrease."""
    baseline = _get_baseline(days)
    factor = 1 + (change_pct / 100)
    new_expenses = baseline["expenses"] * factor
    new_net_profit = baseline["gross_profit"] - new_expenses

    return {
        "scenario": f"Expenses {'increase' if change_pct > 0 else 'decrease'} by {abs(change_pct):.0f}%",
        "change_pct": change_pct,
        "baseline": {"expenses": round(baseline["expenses"], 2), "net_profit": round(baseline["net_profit"], 2)},
        "simulated": {"expenses": round(new_expenses, 2), "net_profit": round(new_net_profit, 2)},
        "impact": {
            "expense_change": round(new_expenses - baseline["expenses"], 2),
            "profit_change": round(new_net_profit - baseline["net_profit"], 2),
        },
        "recommendation": (
            f"Reducing expenses by {abs(change_pct):.0f}% saves NPR {baseline['expenses'] - new_expenses:,.0f}."
            if change_pct < 0 else
            f"Expense increase of {change_pct:.0f}% reduces profit by NPR {baseline['net_profit'] - new_net_profit:,.0f}."
        ),
    }


def simulate_stock_out(product_id: int, days: int = 30) -> dict:
    """Simulate revenue impact if a product goes out of stock."""
    product = db.session.get(Product, product_id)
    if not product:
        return {"error": "Product not found"}

    start = date.today() - timedelta(days=days)
    revenue_from_product = db.session.execute(
        db.select(func.coalesce(func.sum(SaleItem.subtotal), 0))
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(SaleItem.product_id == product_id)
        .where(func.date(Sale.sale_date) >= start)
    ).scalar() or 0

    total_revenue = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= start)
    ).scalar() or 1

    pct_of_revenue = (float(revenue_from_product) / float(total_revenue)) * 100

    return {
        "scenario": f"Stock-out simulation for '{product.name}'",
        "product": {"id": product.id, "name": product.name, "current_stock": product.quantity},
        "revenue_at_risk": round(float(revenue_from_product), 2),
        "pct_of_total_revenue": round(pct_of_revenue, 2),
        "days_of_stock_left": round(
            product.quantity / (float(revenue_from_product) / float(product.selling_price) / days), 1
        ) if float(revenue_from_product) > 0 and float(product.selling_price) > 0 else 999,
        "recommendation": (
            f"'{product.name}' contributes {pct_of_revenue:.1f}% of revenue. "
            f"{'CRITICAL: Restock immediately!' if pct_of_revenue > 10 else 'Monitor stock levels.'}"
        ),
    }


def _sales_change_recommendation(change_pct: float, new_profit: float) -> str:
    if new_profit < 0:
        return "Warning: Even with this change, business would be unprofitable."
    if change_pct > 0:
        return f"A {change_pct:.0f}% sales increase would significantly boost profitability. Focus on marketing."
    return f"A {abs(change_pct):.0f}% sales decline would reduce profits. Build contingency reserves."
