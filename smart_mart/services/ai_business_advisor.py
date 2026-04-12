"""Advanced AI Business Advisor — comprehensive business intelligence and recommendations."""
from __future__ import annotations

from datetime import date, timedelta, datetime
from decimal import Decimal

from sqlalchemy import func

from ..extensions import db
from ..models.sale import Sale, SaleItem
from ..models.product import Product
from ..models.expense import Expense
from ..models.purchase import Purchase
from ..models.customer import Customer
from ..models.supplier import Supplier
from .db_compat import date_format_hour


def _money(v) -> float:
    return float(v or 0)


# ── Core metrics ─────────────────────────────────────────────────────────────

def _period_revenue(start: date, end: date) -> float:
    r = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end)
    ).scalar()
    return _money(r)


def _period_cogs(start: date, end: date) -> float:
    r = db.session.execute(
        db.select(func.coalesce(func.sum(Product.cost_price * SaleItem.quantity), 0))
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end)
    ).scalar()
    return _money(r)


def _period_expenses(start: date, end: date) -> float:
    r = db.session.execute(
        db.select(func.coalesce(func.sum(Expense.amount), 0))
        .where(Expense.expense_date >= start, Expense.expense_date <= end)
    ).scalar()
    return _money(r)


def _period_transactions(start: date, end: date) -> int:
    r = db.session.execute(
        db.select(func.count(Sale.id))
        .where(func.date(Sale.sale_date) >= start, func.date(Sale.sale_date) <= end)
    ).scalar()
    return int(r or 0)


# ── Executive Summary ─────────────────────────────────────────────────────────

def executive_summary() -> dict:
    today = date.today()
    month_start = today.replace(day=1)
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)
    week_start = today - timedelta(days=today.weekday())
    year_start = today.replace(month=1, day=1)

    # Current month
    rev_m = _period_revenue(month_start, today)
    cogs_m = _period_cogs(month_start, today)
    exp_m = _period_expenses(month_start, today)
    profit_m = rev_m - cogs_m - exp_m
    txn_m = _period_transactions(month_start, today)

    # Previous month
    rev_pm = _period_revenue(prev_month_start, prev_month_end)
    cogs_pm = _period_cogs(prev_month_start, prev_month_end)
    exp_pm = _period_expenses(prev_month_start, prev_month_end)
    profit_pm = rev_pm - cogs_pm - exp_pm

    # YTD
    rev_ytd = _period_revenue(year_start, today)
    profit_ytd = rev_ytd - _period_cogs(year_start, today) - _period_expenses(year_start, today)

    # Week
    rev_w = _period_revenue(week_start, today)

    def _pct_change(current, previous):
        if previous == 0:
            return 100.0 if current > 0 else 0.0
        return round((current - previous) / previous * 100, 1)

    # Gross margin
    gross_margin = round((rev_m - cogs_m) / rev_m * 100, 1) if rev_m else 0
    net_margin = round(profit_m / rev_m * 100, 1) if rev_m else 0
    avg_order = round(rev_m / txn_m, 2) if txn_m else 0

    # Stock health
    total_products = db.session.execute(db.select(func.count(Product.id))).scalar() or 0
    low_stock = db.session.execute(
        db.select(func.count(Product.id)).where(Product.quantity <= 10, Product.quantity > 0)
    ).scalar() or 0
    out_of_stock = db.session.execute(
        db.select(func.count(Product.id)).where(Product.quantity == 0)
    ).scalar() or 0
    stock_value = db.session.execute(
        db.select(func.coalesce(func.sum(Product.cost_price * Product.quantity), 0))
    ).scalar() or 0

    # Customer metrics
    total_customers = db.session.execute(db.select(func.count(Customer.id))).scalar() or 0
    new_customers_month = db.session.execute(
        db.select(func.count(Customer.id))
        .where(func.date(Customer.created_at) >= month_start)
    ).scalar() or 0

    return {
        "revenue": {"month": rev_m, "prev_month": rev_pm, "week": rev_w, "ytd": rev_ytd,
                    "change_pct": _pct_change(rev_m, rev_pm)},
        "profit": {"month": profit_m, "prev_month": profit_pm, "ytd": profit_ytd,
                   "change_pct": _pct_change(profit_m, profit_pm)},
        "margins": {"gross": gross_margin, "net": net_margin},
        "transactions": {"month": txn_m, "avg_order": avg_order},
        "stock": {"total": total_products, "low": low_stock, "out": out_of_stock,
                  "value": _money(stock_value)},
        "customers": {"total": total_customers, "new_month": new_customers_month},
        "expenses": {"month": exp_m},
    }


# ── Strategic Recommendations ─────────────────────────────────────────────────

def strategic_recommendations() -> list[dict]:
    today = date.today()
    month_start = today.replace(day=1)
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)

    recs = []

    # 1. Revenue trend
    rev_m = _period_revenue(month_start, today)
    rev_pm = _period_revenue(prev_month_start, prev_month_end)
    if rev_pm > 0:
        rev_change = (rev_m - rev_pm) / rev_pm * 100
        if rev_change < -10:
            recs.append({
                "category": "Revenue", "priority": "critical",
                "icon": "bi-graph-down-arrow", "color": "danger",
                "title": f"Revenue down {abs(rev_change):.0f}% vs last month",
                "insight": f"Revenue dropped from NPR {rev_pm:,.0f} to NPR {rev_m:,.0f}.",
                "action": "Review pricing, run promotions, check if top products are out of stock.",
            })
        elif rev_change > 20:
            recs.append({
                "category": "Revenue", "priority": "positive",
                "icon": "bi-graph-up-arrow", "color": "success",
                "title": f"Revenue up {rev_change:.0f}% — strong growth",
                "insight": f"Revenue grew from NPR {rev_pm:,.0f} to NPR {rev_m:,.0f}.",
                "action": "Ensure stock levels can sustain demand. Consider expanding top categories.",
            })

    # 2. Margin health
    cogs_m = _period_cogs(month_start, today)
    gross_margin = (rev_m - cogs_m) / rev_m * 100 if rev_m else 0
    if gross_margin < 15:
        recs.append({
            "category": "Profitability", "priority": "critical",
            "icon": "bi-exclamation-triangle-fill", "color": "danger",
            "title": f"Gross margin critically low at {gross_margin:.1f}%",
            "insight": "Cost of goods is consuming most of your revenue.",
            "action": "Negotiate better supplier prices. Review products sold below cost. Increase selling prices on low-margin items.",
        })
    elif gross_margin < 25:
        recs.append({
            "category": "Profitability", "priority": "warning",
            "icon": "bi-percent", "color": "warning",
            "title": f"Gross margin at {gross_margin:.1f}% — room to improve",
            "insight": "Industry average for retail is 25-40%.",
            "action": "Identify your 5 lowest-margin products and either reprice or replace them.",
        })

    # 3. Out of stock products
    out_of_stock = db.session.execute(
        db.select(func.count(Product.id)).where(Product.quantity == 0)
    ).scalar() or 0
    if out_of_stock > 0:
        recs.append({
            "category": "Inventory", "priority": "critical" if out_of_stock > 5 else "warning",
            "icon": "bi-box-seam", "color": "danger" if out_of_stock > 5 else "warning",
            "title": f"{out_of_stock} product{'s' if out_of_stock > 1 else ''} out of stock",
            "insight": "Out-of-stock items directly lose sales and damage customer trust.",
            "action": "Create purchase orders immediately for out-of-stock items. Enable reorder alerts.",
        })

    # 4. Dead stock
    cutoff_30 = today - timedelta(days=30)
    dead_count = db.session.execute(
        db.select(func.count(Product.id))
        .where(Product.quantity > 0)
        .where(~Product.id.in_(
            db.select(SaleItem.product_id)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(func.date(Sale.sale_date) >= cutoff_30)
        ))
    ).scalar() or 0
    if dead_count > 0:
        recs.append({
            "category": "Inventory", "priority": "warning",
            "icon": "bi-hourglass-split", "color": "warning",
            "title": f"{dead_count} products with no sales in 30 days",
            "insight": "Dead stock ties up capital and takes up shelf space.",
            "action": "Run clearance promotions. Bundle slow items with fast movers. Consider returning to supplier.",
        })

    # 5. Expense ratio
    exp_m = _period_expenses(month_start, today)
    if rev_m > 0:
        exp_ratio = exp_m / rev_m * 100
        if exp_ratio > 30:
            recs.append({
                "category": "Expenses", "priority": "warning",
                "icon": "bi-receipt", "color": "warning",
                "title": f"Expenses at {exp_ratio:.0f}% of revenue",
                "insight": f"NPR {exp_m:,.0f} in expenses against NPR {rev_m:,.0f} revenue.",
                "action": "Review recurring expenses. Identify areas to cut without impacting operations.",
            })

    # 6. Customer retention
    total_customers = db.session.execute(db.select(func.count(Customer.id))).scalar() or 0
    repeat_customers = db.session.execute(
        db.select(func.count(Customer.id)).where(Customer.visit_count > 1)
    ).scalar() or 0
    if total_customers > 10:
        retention_rate = repeat_customers / total_customers * 100
        if retention_rate < 30:
            recs.append({
                "category": "Customers", "priority": "warning",
                "icon": "bi-people", "color": "info",
                "title": f"Customer retention at {retention_rate:.0f}% — below target",
                "insight": f"Only {repeat_customers} of {total_customers} customers return.",
                "action": "Launch loyalty program. Send follow-up offers. Improve customer experience.",
            })

    # 7. Supplier concentration risk
    supplier_count = db.session.execute(db.select(func.count(Supplier.id))).scalar() or 0
    if supplier_count < 3:
        recs.append({
            "category": "Supply Chain", "priority": "warning",
            "icon": "bi-truck", "color": "warning",
            "title": "Low supplier diversity — concentration risk",
            "insight": f"Only {supplier_count} supplier{'s' if supplier_count != 1 else ''} on record.",
            "action": "Add backup suppliers for your top 5 products to avoid stockouts if one supplier fails.",
        })

    # 8. Payment mode analysis
    credit_sales = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(Sale.payment_mode == "credit",
               func.date(Sale.sale_date) >= month_start)
    ).scalar() or 0
    if rev_m > 0 and _money(credit_sales) / rev_m > 0.3:
        recs.append({
            "category": "Cash Flow", "priority": "warning",
            "icon": "bi-credit-card-2-back", "color": "warning",
            "title": f"Credit sales at {_money(credit_sales)/rev_m*100:.0f}% of revenue",
            "insight": "High credit exposure can cause cash flow problems.",
            "action": "Set credit limits per customer. Follow up on overdue collections. Offer small discounts for cash payment.",
        })

    # Sort: critical first
    priority_order = {"critical": 0, "warning": 1, "positive": 2, "info": 3}
    recs.sort(key=lambda r: priority_order.get(r["priority"], 9))
    return recs


# ── Growth Opportunities ──────────────────────────────────────────────────────

def growth_opportunities() -> list[dict]:
    today = date.today()
    month_start = today.replace(day=1)
    opportunities = []

    # Top category by revenue
    top_cat = db.session.execute(
        db.select(Product.category, func.sum(SaleItem.subtotal).label("rev"))
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= month_start)
        .group_by(Product.category)
        .order_by(func.sum(SaleItem.subtotal).desc())
        .limit(1)
    ).first()
    if top_cat:
        opportunities.append({
            "icon": "bi-star-fill", "color": "#f59e0b",
            "title": f"Expand '{top_cat.category}' category",
            "detail": f"Your top category this month with NPR {float(top_cat.rev):,.0f} revenue. Add more SKUs.",
        })

    # High margin products
    high_margin = db.session.execute(
        db.select(Product)
        .where(Product.selling_price > Product.cost_price * 1.4, Product.quantity > 0)
        .order_by((Product.selling_price - Product.cost_price).desc())
        .limit(3)
    ).scalars().all()
    if high_margin:
        names = ", ".join(p.name for p in high_margin[:2])
        opportunities.append({
            "icon": "bi-graph-up", "color": "#22c55e",
            "title": "Push high-margin products",
            "detail": f"{names} have 40%+ margins. Feature them prominently and train staff to upsell.",
        })

    # Underperforming hours (if enough data)
    hourly = db.session.execute(
        db.select(
            date_format_hour(Sale.sale_date).label("hour"),
            func.count(Sale.id).label("cnt")
        )
        .where(func.date(Sale.sale_date) >= today - timedelta(days=30))
        .group_by(date_format_hour(Sale.sale_date))
        .order_by(func.count(Sale.id))
        .limit(3)
    ).all()
    if hourly:
        slow_hours = [f"{int(r.hour):02d}:00" for r in hourly]
        opportunities.append({
            "icon": "bi-clock", "color": "#6366f1",
            "title": "Boost slow-hour sales",
            "detail": f"Slowest hours: {', '.join(slow_hours)}. Run time-limited offers during these periods.",
        })

    # Loyalty upsell
    loyalty_customers = db.session.execute(
        db.select(func.count(Customer.id)).where(Customer.visit_count >= 3)
    ).scalar() or 0
    if loyalty_customers > 0:
        opportunities.append({
            "icon": "bi-heart-fill", "color": "#ec4899",
            "title": f"Activate {loyalty_customers} loyal customers",
            "detail": "These customers visit 3+ times. Send them exclusive offers to increase basket size.",
        })

    return opportunities


# ── 30-Day Forecast ───────────────────────────────────────────────────────────

def revenue_forecast_30d() -> dict:
    today = date.today()
    # Use last 90 days as baseline
    start = today - timedelta(days=89)
    rows = db.session.execute(
        db.select(func.date(Sale.sale_date).label("day"), func.sum(Sale.total_amount).label("rev"))
        .where(func.date(Sale.sale_date) >= start)
        .group_by(func.date(Sale.sale_date))
        .order_by(func.date(Sale.sale_date))
    ).all()

    if not rows:
        return {"labels": [], "forecast": [], "avg_daily": 0, "projected_monthly": 0}

    daily_revs = [float(r.rev) for r in rows]
    avg_daily = sum(daily_revs) / len(daily_revs)

    # Simple linear trend
    n = len(daily_revs)
    if n >= 7:
        x_mean = (n - 1) / 2
        y_mean = avg_daily
        num = sum((i - x_mean) * (daily_revs[i] - y_mean) for i in range(n))
        den = sum((i - x_mean) ** 2 for i in range(n))
        slope = num / den if den else 0
    else:
        slope = 0

    labels, forecast = [], []
    for i in range(30):
        d = today + timedelta(days=i + 1)
        labels.append(d.strftime("%b %d"))
        projected = max(0, avg_daily + slope * (n + i))
        forecast.append(round(projected, 2))

    return {
        "labels": labels,
        "forecast": forecast,
        "avg_daily": round(avg_daily, 2),
        "projected_monthly": round(sum(forecast), 2),
        "trend": "up" if slope > 0 else "down" if slope < 0 else "flat",
        "trend_pct": round(slope / avg_daily * 100, 1) if avg_daily else 0,
    }


# ── KPI Scorecard ─────────────────────────────────────────────────────────────

def kpi_scorecard() -> list[dict]:
    today = date.today()
    month_start = today.replace(day=1)
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)

    rev_m = _period_revenue(month_start, today)
    rev_pm = _period_revenue(prev_month_start, prev_month_end)
    cogs_m = _period_cogs(month_start, today)
    exp_m = _period_expenses(month_start, today)
    profit_m = rev_m - cogs_m - exp_m
    txn_m = _period_transactions(month_start, today)
    avg_order = rev_m / txn_m if txn_m else 0

    total_customers = db.session.execute(db.select(func.count(Customer.id))).scalar() or 0
    repeat = db.session.execute(
        db.select(func.count(Customer.id)).where(Customer.visit_count > 1)
    ).scalar() or 0
    retention = repeat / total_customers * 100 if total_customers else 0

    gross_margin = (rev_m - cogs_m) / rev_m * 100 if rev_m else 0

    def _score(value, target, higher_is_better=True):
        if target == 0:
            return 50
        ratio = value / target
        if higher_is_better:
            return min(100, int(ratio * 100))
        else:
            return max(0, int((2 - ratio) * 50))

    return [
        {"kpi": "Monthly Revenue", "value": f"NPR {rev_m:,.0f}", "target": f"NPR {rev_pm:,.0f}",
         "score": _score(rev_m, rev_pm), "trend": "up" if rev_m >= rev_pm else "down"},
        {"kpi": "Gross Margin", "value": f"{gross_margin:.1f}%", "target": "25%",
         "score": _score(gross_margin, 25), "trend": "up" if gross_margin >= 25 else "down"},
        {"kpi": "Net Profit", "value": f"NPR {profit_m:,.0f}", "target": f"NPR {rev_m*0.1:,.0f}",
         "score": _score(profit_m, rev_m * 0.1), "trend": "up" if profit_m > 0 else "down"},
        {"kpi": "Avg Order Value", "value": f"NPR {avg_order:,.0f}", "target": "NPR 500",
         "score": _score(avg_order, 500), "trend": "up" if avg_order >= 500 else "down"},
        {"kpi": "Customer Retention", "value": f"{retention:.0f}%", "target": "40%",
         "score": _score(retention, 40), "trend": "up" if retention >= 40 else "down"},
        {"kpi": "Expense Ratio", "value": f"{exp_m/rev_m*100:.0f}%" if rev_m else "N/A",
         "target": "<20%", "score": _score(exp_m / rev_m * 100 if rev_m else 100, 20, False),
         "trend": "up" if rev_m and exp_m / rev_m < 0.2 else "down"},
    ]


# ── Full advisor report ───────────────────────────────────────────────────────

def full_advisor_report() -> dict:
    return {
        "summary": executive_summary(),
        "recommendations": strategic_recommendations(),
        "opportunities": growth_opportunities(),
        "forecast": revenue_forecast_30d(),
        "kpis": kpi_scorecard(),
        "product_actions": product_action_recommendations(),
        "generated_at": datetime.now().isoformat(),
    }


# ── Product Action Advisor ────────────────────────────────────────────────────

def product_action_recommendations() -> list[dict]:
    """
    Per-product AI recommendations:
    - "Increase price" — high demand, healthy stock, low margin
    - "Stop stocking" — dead stock + low/negative margin
    - "Buy more" — fast moving + low stock
    - "Run promotion" — slow moving but decent margin
    - "Review pricing" — selling below cost
    """
    today = date.today()
    days_30 = today - timedelta(days=30)
    days_90 = today - timedelta(days=90)

    # Sales velocity per product (last 30 days)
    velocity_rows = db.session.execute(
        db.select(
            SaleItem.product_id,
            func.sum(SaleItem.quantity).label("qty_sold_30d"),
            func.sum(SaleItem.subtotal).label("revenue_30d"),
        )
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= days_30)
        .group_by(SaleItem.product_id)
    ).all()
    velocity = {r.product_id: {"qty": int(r.qty_sold_30d), "rev": float(r.revenue_30d)} for r in velocity_rows}

    # Sales in last 90 days (for dead stock check)
    sold_90d = set(db.session.execute(
        db.select(SaleItem.product_id.distinct())
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= days_90)
    ).scalars().all())

    # All active products
    products = db.session.execute(
        db.select(Product).where(Product.quantity >= 0)
        .order_by(Product.name)
    ).scalars().all()

    # Average daily sales across all products (for "fast" threshold)
    total_daily_avg = db.session.execute(
        db.select(func.coalesce(func.sum(SaleItem.quantity), 0))
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= days_30)
    ).scalar() or 0
    avg_daily_units = float(total_daily_avg) / 30 if total_daily_avg else 1

    actions = []

    for p in products:
        cost = float(p.cost_price or 0)
        price = float(p.selling_price or 0)
        qty = p.quantity
        margin_pct = ((price - cost) / price * 100) if price > 0 else 0
        v = velocity.get(p.id, {"qty": 0, "rev": 0.0})
        qty_sold_30d = v["qty"]
        daily_velocity = qty_sold_30d / 30

        # ── Rule 1: Selling below cost → Review pricing immediately ──────
        if cost > 0 and price < cost:
            actions.append({
                "product_id": p.id,
                "product_name": p.name,
                "action": "review_pricing",
                "action_label": "⚠️ Review Pricing",
                "color": "danger",
                "priority": 1,
                "reason": f"Selling at NPR {price:,.0f} but costs NPR {cost:,.0f} — losing NPR {cost-price:,.0f} per unit.",
                "recommendation": f"Increase price to at least NPR {cost * 1.15:,.0f} (15% margin).",
                "data": {"cost": cost, "price": price, "margin_pct": round(margin_pct, 1), "qty_sold_30d": qty_sold_30d},
            })

        # ── Rule 2: Stop stocking — no sales in 90 days + low margin ─────
        elif p.id not in sold_90d and qty > 0 and margin_pct < 20:
            actions.append({
                "product_id": p.id,
                "product_name": p.name,
                "action": "stop_stocking",
                "action_label": "🚫 Stop Stocking",
                "color": "danger",
                "priority": 2,
                "reason": f"No sales in 90+ days. {qty} units sitting idle. Margin only {margin_pct:.0f}%.",
                "recommendation": f"Clear remaining {qty} units via discount or return to supplier. Do not reorder.",
                "data": {"qty_in_stock": qty, "days_no_sale": 90, "margin_pct": round(margin_pct, 1)},
            })

        # ── Rule 3: Buy more — fast moving + low stock ────────────────────
        elif daily_velocity > 0 and qty > 0:
            days_of_stock = qty / daily_velocity
            if days_of_stock < 7 and qty_sold_30d >= 5:
                actions.append({
                    "product_id": p.id,
                    "product_name": p.name,
                    "action": "buy_more",
                    "action_label": "🛒 Buy More",
                    "color": "success",
                    "priority": 3,
                    "reason": f"Selling {qty_sold_30d} units/month. Only {qty} units left — {days_of_stock:.0f} days of stock.",
                    "recommendation": f"Order at least {max(qty_sold_30d, 30)} units to cover 30 days of demand.",
                    "data": {"qty_in_stock": qty, "qty_sold_30d": qty_sold_30d, "days_of_stock": round(days_of_stock, 1)},
                })

        # ── Rule 4: Increase price — high demand + low margin ─────────────
        elif qty_sold_30d >= 10 and 0 < margin_pct < 20 and price > cost:
            suggested_price = round(cost * 1.25, -1)  # 25% margin, rounded to 10s
            actions.append({
                "product_id": p.id,
                "product_name": p.name,
                "action": "increase_price",
                "action_label": "💰 Increase Price",
                "color": "warning",
                "priority": 4,
                "reason": f"Selling {qty_sold_30d} units/month but margin is only {margin_pct:.0f}%. High demand = pricing power.",
                "recommendation": f"Increase price from NPR {price:,.0f} to NPR {suggested_price:,.0f} to reach 25% margin.",
                "data": {"current_price": price, "suggested_price": suggested_price, "margin_pct": round(margin_pct, 1), "qty_sold_30d": qty_sold_30d},
            })

        # ── Rule 5: Run promotion — slow moving but good margin ───────────
        elif p.id not in sold_90d and qty > 0 and margin_pct >= 30:
            discount_price = round(price * 0.85, -1)
            actions.append({
                "product_id": p.id,
                "product_name": p.name,
                "action": "run_promotion",
                "action_label": "🏷️ Run Promotion",
                "color": "info",
                "priority": 5,
                "reason": f"No sales in 90 days but {margin_pct:.0f}% margin gives room for a discount.",
                "recommendation": f"Offer 15% off (NPR {discount_price:,.0f}) to clear {qty} units and free up capital.",
                "data": {"qty_in_stock": qty, "margin_pct": round(margin_pct, 1), "suggested_promo_price": discount_price},
            })

        # ── Rule 6: Out of stock — was selling well ───────────────────────
        elif qty == 0 and qty_sold_30d >= 3:
            actions.append({
                "product_id": p.id,
                "product_name": p.name,
                "action": "restock_urgent",
                "action_label": "🚨 Restock Now",
                "color": "danger",
                "priority": 1,
                "reason": f"Out of stock! Was selling {qty_sold_30d} units last month — losing sales every day.",
                "recommendation": f"Order immediately. Estimated lost revenue: NPR {qty_sold_30d * price:,.0f}/month.",
                "data": {"qty_sold_30d": qty_sold_30d, "estimated_lost_revenue": round(qty_sold_30d * price, 2)},
            })

    # Sort by priority then by impact
    actions.sort(key=lambda x: (x["priority"], -x["data"].get("qty_sold_30d", 0)))
    return actions[:30]  # top 30 most actionable
