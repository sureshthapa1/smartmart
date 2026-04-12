"""AI Engine — demand prediction, restock recommendations, insights, forecasting, chatbot.

Uses pure Python statistics (moving averages, linear regression, rule-based logic).
No external ML libraries required.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from sqlalchemy import and_, func

from ..extensions import db
from ..models.product import Product
from ..models.sale import Sale, SaleItem


# ── Helpers ───────────────────────────────────────────────────────────────────

def _daily_sales_series(product_id: int, days: int = 90) -> list[float]:
    """Return a list of daily quantities sold for a product over the past N days."""
    end = date.today()
    start = end - timedelta(days=days - 1)
    rows = db.session.execute(
        db.select(
            func.date(Sale.sale_date).label("day"),
            func.sum(SaleItem.quantity).label("qty"),
        )
        .join(SaleItem, SaleItem.sale_id == Sale.id)
        .where(SaleItem.product_id == product_id)
        .where(func.date(Sale.sale_date) >= start)
        .group_by(func.date(Sale.sale_date))
        .order_by(func.date(Sale.sale_date))
    ).all()

    # Build a full series (0 for days with no sales)
    sales_map = {str(r.day): float(r.qty) for r in rows}
    series = []
    current = start
    while current <= end:
        series.append(sales_map.get(str(current), 0.0))
        current += timedelta(days=1)
    return series


def _moving_average(series: list[float], window: int = 7) -> float:
    """Return the moving average of the last `window` values."""
    if not series:
        return 0.0
    tail = series[-window:] if len(series) >= window else series
    return sum(tail) / len(tail)


def _linear_regression(series: list[float]) -> tuple[float, float]:
    """Simple OLS linear regression. Returns (slope, intercept)."""
    n = len(series)
    if n < 2:
        return 0.0, series[0] if series else 0.0
    x_mean = (n - 1) / 2
    y_mean = sum(series) / n
    num = sum((i - x_mean) * (series[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    slope = num / den if den else 0.0
    intercept = y_mean - slope * x_mean
    return slope, intercept


def _total_sold(product_id: int, days: int) -> float:
    """Total quantity sold for a product in the last N days."""
    cutoff = date.today() - timedelta(days=days)
    result = db.session.execute(
        db.select(func.coalesce(func.sum(SaleItem.quantity), 0))
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(SaleItem.product_id == product_id)
        .where(func.date(Sale.sale_date) >= cutoff)
    ).scalar() or 0
    return float(result)


# ── 1. Demand Prediction ──────────────────────────────────────────────────────

def demand_prediction(product_id: int) -> dict:
    """Predict daily demand for a product using weighted moving average.

    Returns:
        avg_daily_demand: average units sold per day (last 30d)
        trend: 'rising' | 'falling' | 'stable'
        confidence: 'high' | 'medium' | 'low'
        predicted_weekly: estimated units needed next 7 days
    """
    series_90 = _daily_sales_series(product_id, days=90)
    series_30 = series_90[-30:]
    series_7 = series_90[-7:]

    avg_30 = _moving_average(series_30, window=30)
    avg_7 = _moving_average(series_7, window=7)
    slope, _ = _linear_regression(series_30)

    # Trend detection
    if slope > 0.05:
        trend = "rising"
    elif slope < -0.05:
        trend = "falling"
    else:
        trend = "stable"

    # Confidence based on data density
    non_zero = sum(1 for v in series_30 if v > 0)
    if non_zero >= 20:
        confidence = "high"
    elif non_zero >= 10:
        confidence = "medium"
    else:
        confidence = "low"

    # Weighted prediction: 60% recent 7d avg + 40% 30d avg
    predicted_daily = 0.6 * avg_7 + 0.4 * avg_30
    predicted_weekly = round(predicted_daily * 7, 1)

    return {
        "avg_daily_demand": round(avg_30, 2),
        "recent_7d_avg": round(avg_7, 2),
        "trend": trend,
        "confidence": confidence,
        "predicted_weekly": predicted_weekly,
        "slope": round(slope, 4),
    }


# ── 2. Smart Restock Recommendation ──────────────────────────────────────────

def restock_recommendation(product_id: int, lead_time_days: int = 3) -> dict:
    """Calculate smart restock quantity.

    Formula:
        reorder_point = avg_daily_demand * (lead_time + safety_days)
        recommended_qty = reorder_point * 2 - current_stock  (if below reorder point)

    Returns:
        should_restock: bool
        reorder_point: units
        recommended_qty: units to order
        days_of_stock_left: estimated days before stockout
        urgency: 'critical' | 'soon' | 'ok'
    """
    product = db.session.get(Product, product_id)
    if not product:
        return {}

    pred = demand_prediction(product_id)
    avg_daily = pred["avg_daily_demand"]
    current_stock = product.quantity

    if avg_daily <= 0:
        return {
            "should_restock": False,
            "reorder_point": 0,
            "recommended_qty": 0,
            "days_of_stock_left": 999,
            "urgency": "ok",
            "reason": "No recent sales data.",
        }

    safety_days = 7  # buffer
    reorder_point = math.ceil(avg_daily * (lead_time_days + safety_days))
    days_left = current_stock / avg_daily if avg_daily > 0 else 999
    should_restock = current_stock <= reorder_point

    if days_left <= lead_time_days:
        urgency = "critical"
    elif days_left <= lead_time_days + safety_days:
        urgency = "soon"
    else:
        urgency = "ok"

    # Recommend enough for 30 days
    recommended_qty = max(0, math.ceil(avg_daily * 30) - current_stock)

    return {
        "should_restock": should_restock,
        "reorder_point": reorder_point,
        "recommended_qty": recommended_qty,
        "days_of_stock_left": round(days_left, 1),
        "urgency": urgency,
        "avg_daily_demand": round(avg_daily, 2),
        "current_stock": current_stock,
    }


# ── 3. Business Insights ──────────────────────────────────────────────────────

def generate_insights() -> list[dict]:
    """Auto-generate business insights from current data."""
    insights = []
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    last_week_start = week_start - timedelta(days=7)
    month_start = today.replace(day=1)

    # ── Sales trend ───────────────────────────────────────────────────────
    this_week = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= week_start)
    ).scalar() or 0
    last_week = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= last_week_start)
        .where(func.date(Sale.sale_date) < week_start)
    ).scalar() or 0

    if float(last_week) > 0:
        pct = ((float(this_week) - float(last_week)) / float(last_week)) * 100
        if pct >= 10:
            insights.append({
                "type": "success", "icon": "📈",
                "title": "Sales Surge",
                "text": f"Sales are up {pct:.1f}% compared to last week. Great performance!",
                "priority": 1,
            })
        elif pct <= -10:
            insights.append({
                "type": "warning", "icon": "📉",
                "title": "Sales Decline",
                "text": f"Sales dropped {abs(pct):.1f}% vs last week. Consider promotions.",
                "priority": 2,
            })

    # ── Top product this month ────────────────────────────────────────────
    top = db.session.execute(
        db.select(Product, func.sum(SaleItem.quantity).label("qty"))
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= month_start)
        .group_by(Product.id)
        .order_by(func.sum(SaleItem.quantity).desc())
        .limit(1)
    ).first()
    if top:
        insights.append({
            "type": "info", "icon": "🏆",
            "title": "Top Seller",
            "text": f"{top.Product.name} is your best-selling product this month ({top.qty} units sold).",
            "priority": 3,
        })

    # ── Low stock critical ────────────────────────────────────────────────
    critical = db.session.execute(
        db.select(func.count(Product.id)).where(Product.quantity == 0)
    ).scalar() or 0
    if critical > 0:
        insights.append({
            "type": "danger", "icon": "🚨",
            "title": "Out of Stock",
            "text": f"{critical} product(s) are completely out of stock. Restock immediately!",
            "priority": 1,
        })

    low = db.session.execute(
        db.select(func.count(Product.id)).where(Product.quantity <= 10).where(Product.quantity > 0)
    ).scalar() or 0
    if low > 0:
        insights.append({
            "type": "warning", "icon": "⚠️",
            "title": "Low Stock Warning",
            "text": f"{low} product(s) have stock ≤ 10 units. Consider restocking soon.",
            "priority": 2,
        })

    # ── Dead stock ────────────────────────────────────────────────────────
    cutoff = today - timedelta(days=30)
    sold_ids = db.session.execute(
        db.select(SaleItem.product_id.distinct())
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= cutoff)
    ).scalars().all()
    dead_count = db.session.execute(
        db.select(func.count(Product.id))
        .where(Product.id.notin_(sold_ids) if sold_ids else db.true())
        .where(Product.quantity > 0)
    ).scalar() or 0
    if dead_count > 0:
        insights.append({
            "type": "secondary", "icon": "💤",
            "title": "Dead Stock",
            "text": f"{dead_count} product(s) have had no sales in 30 days. Consider discounts or removal.",
            "priority": 3,
        })

    # ── Best sales day ────────────────────────────────────────────────────
    best_day = db.session.execute(
        db.select(func.date(Sale.sale_date).label("day"), func.sum(Sale.total_amount).label("total"))
        .where(func.date(Sale.sale_date) >= month_start)
        .group_by(func.date(Sale.sale_date))
        .order_by(func.sum(Sale.total_amount).desc())
        .limit(1)
    ).first()
    if best_day:
        insights.append({
            "type": "info", "icon": "⭐",
            "title": "Best Day This Month",
            "text": f"Your best sales day was {best_day.day} with NPR {float(best_day.total):,.0f} in revenue.",
            "priority": 4,
        })

    # Sort by priority
    insights.sort(key=lambda x: x["priority"])
    return insights


# ── 4. Dead Stock Detection ───────────────────────────────────────────────────

def detect_dead_stock(days: int = 30) -> list[dict]:
    """Detect products with no sales in the past N days."""
    cutoff = date.today() - timedelta(days=days)
    sold_ids = db.session.execute(
        db.select(SaleItem.product_id.distinct())
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= cutoff)
    ).scalars().all()

    products = db.session.execute(
        db.select(Product)
        .where(Product.id.notin_(sold_ids) if sold_ids else db.true())
        .where(Product.quantity > 0)
        .order_by(Product.quantity.desc())
    ).scalars().all()

    result = []
    for p in products:
        # Last sale date
        last_sale = db.session.execute(
            db.select(func.max(Sale.sale_date))
            .join(SaleItem, SaleItem.sale_id == Sale.id)
            .where(SaleItem.product_id == p.id)
        ).scalar()

        days_since = (date.today() - last_sale.date()).days if last_sale else None
        stock_value = float(p.cost_price) * p.quantity

        result.append({
            "product": p,
            "last_sale": last_sale,
            "days_since_sale": days_since,
            "stock_value": stock_value,
            "recommendation": "Consider discount or clearance sale" if stock_value > 500
                              else "Consider removing from inventory",
        })
    return result


# ── 5. Sales Forecasting ──────────────────────────────────────────────────────

def forecast_sales(days_ahead: int = 7) -> list[dict]:
    """Forecast total daily sales for the next N days using linear regression on past 60 days."""
    end = date.today()
    start = end - timedelta(days=59)

    rows = db.session.execute(
        db.select(
            func.date(Sale.sale_date).label("day"),
            func.sum(Sale.total_amount).label("total"),
        )
        .where(func.date(Sale.sale_date) >= start)
        .group_by(func.date(Sale.sale_date))
        .order_by(func.date(Sale.sale_date))
    ).all()

    # Build full 60-day series
    sales_map = {str(r.day): float(r.total) for r in rows}
    series = []
    current = start
    while current <= end:
        series.append(sales_map.get(str(current), 0.0))
        current += timedelta(days=1)

    slope, intercept = _linear_regression(series)
    n = len(series)
    ma7 = _moving_average(series, window=7)

    forecasts = []
    for i in range(1, days_ahead + 1):
        future_date = end + timedelta(days=i)
        # Blend: 50% linear trend + 50% moving average
        trend_val = intercept + slope * (n + i - 1)
        blended = max(0.0, 0.5 * trend_val + 0.5 * ma7)

        # Day-of-week adjustment (simple: weekends typically higher)
        dow = future_date.weekday()
        dow_factor = 1.15 if dow in (4, 5) else (0.9 if dow == 6 else 1.0)
        predicted = round(blended * dow_factor, 2)

        forecasts.append({
            "date": str(future_date),
            "day_name": future_date.strftime("%A"),
            "predicted_sales": predicted,
            "confidence_low": round(predicted * 0.75, 2),
            "confidence_high": round(predicted * 1.25, 2),
        })

    return forecasts


def forecast_product_demand(product_id: int, days_ahead: int = 7) -> list[dict]:
    """Forecast daily demand for a specific product."""
    series = _daily_sales_series(product_id, days=60)
    slope, intercept = _linear_regression(series)
    n = len(series)
    ma7 = _moving_average(series, window=7)

    forecasts = []
    for i in range(1, days_ahead + 1):
        future_date = date.today() + timedelta(days=i)
        trend_val = intercept + slope * (n + i - 1)
        predicted = max(0.0, round(0.5 * trend_val + 0.5 * ma7, 2))
        forecasts.append({
            "date": str(future_date),
            "day_name": future_date.strftime("%A"),
            "predicted_qty": predicted,
        })
    return forecasts


# ── 6. Chatbot Assistant ──────────────────────────────────────────────────────

def chatbot_query(message: str) -> str:
    """Enhanced chatbot — priority-ordered keyword matching with rich responses."""
    msg = message.lower().strip()
    today = date.today()
    month_start = today.replace(day=1)
    week_start = today - timedelta(days=today.weekday())
    yesterday = today - timedelta(days=1)

    def _rev(start, end=None):
        stmt = db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        stmt = stmt.where(func.date(Sale.sale_date) >= start)
        if end:
            stmt = stmt.where(func.date(Sale.sale_date) <= end)
        return float(db.session.execute(stmt).scalar() or 0)

    def _cnt(start, end=None):
        stmt = db.select(func.count(Sale.id))
        stmt = stmt.where(func.date(Sale.sale_date) >= start)
        if end:
            stmt = stmt.where(func.date(Sale.sale_date) <= end)
        return int(db.session.execute(stmt).scalar() or 0)

    def _has(*words):
        return any(w in msg for w in words)

    # ── Greetings (only if message is JUST a greeting) ────────────────────
    if msg in ("hello", "hi", "hey", "namaste", "namaskar") or \
       (len(msg.split()) <= 2 and _has("hello", "hi", "hey", "namaste", "namaskar")):
        return "👋 Namaste! I'm your Smart Mart AI assistant.\n\nAsk me anything about your business — sales, stock, profit, customers, forecasts. Type **help** to see all I can do."

    # ── Help ──────────────────────────────────────────────────────────────
    if _has("help", "what can you", "commands", "what do you know"):
        return ("🤖 **I can answer questions about:**\n\n"
                "📊 **Sales:** today, yesterday, this week, this month, last month\n"
                "💰 **Finance:** profit, expenses, cash balance\n"
                "📦 **Inventory:** low stock, dead stock, stock of [product]\n"
                "👥 **Customers:** top customers, credit/udharo, loyalty points\n"
                "🏆 **Products:** top seller, best product, least selling\n"
                "🔮 **Forecast:** tomorrow, next week prediction\n"
                "⚠️ **Alerts:** what needs attention\n\n"
                "Try: *'today sales'*, *'low stock'*, *'top product'*, *'profit this month'*")

    # ── PROFIT (check BEFORE monthly sales to avoid conflict) ─────────────
    if _has("profit", "earning", "nafa", "margin"):
        try:
            from . import cash_flow_manager
            pl = cash_flow_manager.profit_loss(month_start, today)
            rev = float(pl.get('revenue', 0))
            profit = float(pl.get('profit', 0))
            exp = float(pl.get('expenses', 0))
            margin = (profit / rev * 100) if rev > 0 else 0
            return (f"💰 **This Month's Profit**\n"
                    f"Revenue: NPR {rev:,.2f}\n"
                    f"Expenses: NPR {exp:,.2f}\n"
                    f"Net Profit: NPR {profit:,.2f}\n"
                    f"Margin: {margin:.1f}%")
        except Exception:
            return "💰 Profit data unavailable right now."

    # ── EXPENSES (check before "cost" which is too broad) ─────────────────
    if _has("expense", "kharcha", "spending") or \
       (msg in ("cost", "costs") or "total expense" in msg or "monthly expense" in msg):
        from ..models.expense import Expense
        total_exp = db.session.execute(
            db.select(func.coalesce(func.sum(Expense.amount), 0))
            .where(Expense.expense_date >= month_start)
        ).scalar() or 0
        by_type = db.session.execute(
            db.select(Expense.expense_type, func.sum(Expense.amount).label("total"))
            .where(Expense.expense_date >= month_start)
            .group_by(Expense.expense_type)
            .order_by(func.sum(Expense.amount).desc())
        ).all()
        breakdown = "\n".join(f"  • {r.expense_type}: NPR {float(r.total):,.0f}" for r in by_type)
        return f"💸 **This Month's Expenses**\nTotal: NPR {float(total_exp):,.2f}\n\n{breakdown}"

    # ── CASH BALANCE (specific phrase only) ───────────────────────────────
    if "cash balance" in msg or "kitna paisa" in msg or msg in ("balance", "cash"):
        try:
            from . import cash_flow_manager
            balance = cash_flow_manager.daily_balance(today)
            return f"💵 **Today's Cash Balance:** NPR {float(balance):,.2f}"
        except Exception:
            return "💵 Cash balance unavailable."

    # ── FORECAST (check before "tomorrow" which could match other things) ──
    if _has("forecast", "predict", "bholi") or \
       "next week" in msg or "tomorrow sale" in msg or msg == "tomorrow":
        forecasts = forecast_sales(days_ahead=7)
        if forecasts:
            tomorrow = forecasts[0]
            week_total = sum(f["predicted_sales"] for f in forecasts)
            lines = "\n".join(f"  {f['day_name']}: NPR {f['predicted_sales']:,.0f}" for f in forecasts[:5])
            return (f"🔮 **Sales Forecast**\n"
                    f"Tomorrow ({tomorrow['day_name']}): NPR {tomorrow['predicted_sales']:,.0f}\n"
                    f"Next 7 days: NPR {week_total:,.0f}\n\n"
                    f"Daily breakdown:\n{lines}")
        return "🔮 Not enough data for forecast yet. Need at least 7 days of sales history."

    # ── TODAY'S SALES ─────────────────────────────────────────────────────
    if _has("today", "aaj") and _has("sale", "revenue", "income", "earn", "becha"):
        total = _rev(today)
        count = _cnt(today)
        profit_today = 0
        try:
            cogs = db.session.execute(
                db.select(func.coalesce(func.sum(Product.cost_price * SaleItem.quantity), 0))
                .join(SaleItem, SaleItem.product_id == Product.id)
                .join(Sale, Sale.id == SaleItem.sale_id)
                .where(func.date(Sale.sale_date) == today)
            ).scalar() or 0
            profit_today = total - float(cogs)
        except Exception:
            pass
        if total == 0:
            return f"📊 No sales recorded today yet.\n\nYesterday's sales: NPR {_rev(yesterday, yesterday):,.0f}"
        return (f"📊 **Today's Sales**\n"
                f"Revenue: NPR {total:,.2f}\n"
                f"Transactions: {count}\n"
                f"Avg order: NPR {total/count:,.0f}\n"
                f"Est. profit: NPR {profit_today:,.0f}")

    # ── YESTERDAY ─────────────────────────────────────────────────────────
    if _has("yesterday", "kal") and not _has("forecast"):
        total = _rev(yesterday, yesterday)
        count = _cnt(yesterday, yesterday)
        return f"📅 **Yesterday's Sales**\nRevenue: NPR {total:,.2f} from {count} transaction(s)."

    # ── LAST MONTH (check before "this month") ────────────────────────────
    if _has("last month", "pichlo mahina"):
        prev_end = month_start - timedelta(days=1)
        prev_start = prev_end.replace(day=1)
        total = _rev(prev_start, prev_end)
        count = _cnt(prev_start, prev_end)
        return f"📆 **Last Month's Sales**\nRevenue: NPR {total:,.2f} from {count} transaction(s)."

    # ── THIS WEEK ─────────────────────────────────────────────────────────
    if _has("this week", "weekly", "week sale", "hapta"):
        total = _rev(week_start)
        count = _cnt(week_start)
        daily_avg = total / max((today - week_start).days + 1, 1)
        return (f"📅 **This Week's Sales**\n"
                f"Revenue: NPR {total:,.2f}\n"
                f"Transactions: {count}\n"
                f"Daily avg: NPR {daily_avg:,.0f}")

    # ── THIS MONTH SALES ──────────────────────────────────────────────────
    if _has("this month", "monthly", "month sale", "mahina"):
        total = _rev(month_start)
        count = _cnt(month_start)
        days_passed = (today - month_start).days + 1
        projected = total / days_passed * 30
        return (f"📆 **This Month's Sales**\n"
                f"Revenue: NPR {total:,.2f}\n"
                f"Transactions: {count}\n"
                f"Projected month-end: NPR {projected:,.0f}")

    # ── TOP PRODUCTS ──────────────────────────────────────────────────────
    if _has("top product", "best seller", "best selling", "most sold", "popular"):
        rows = db.session.execute(
            db.select(Product, func.sum(SaleItem.quantity).label("qty"),
                      func.sum(SaleItem.subtotal).label("rev"))
            .join(SaleItem, SaleItem.product_id == Product.id)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(func.date(Sale.sale_date) >= month_start)
            .group_by(Product.id)
            .order_by(func.sum(SaleItem.quantity).desc())
            .limit(5)
        ).all()
        if rows:
            lines = "\n".join(f"  {i+1}. {r.Product.name} — {r.qty} units (NPR {float(r.rev):,.0f})" for i, r in enumerate(rows))
            return f"🏆 **Top 5 Products This Month**\n{lines}"
        return "No sales data this month yet."

    # ── LEAST SELLING ─────────────────────────────────────────────────────
    if _has("least selling", "slow moving", "worst product"):
        rows = db.session.execute(
            db.select(Product, func.sum(SaleItem.quantity).label("qty"))
            .join(SaleItem, SaleItem.product_id == Product.id)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(func.date(Sale.sale_date) >= month_start)
            .group_by(Product.id)
            .order_by(func.sum(SaleItem.quantity).asc())
            .limit(5)
        ).all()
        if rows:
            lines = "\n".join(f"  {i+1}. {r.Product.name} — {r.qty} units" for i, r in enumerate(rows))
            return f"📉 **Least Selling Products This Month**\n{lines}"
        return "No sales data this month yet."

    # ── LOW STOCK ─────────────────────────────────────────────────────────
    if _has("low stock", "stock low", "running out", "restock", "kam stock"):
        products = db.session.execute(
            db.select(Product).where(Product.quantity <= 10).order_by(Product.quantity).limit(8)
        ).scalars().all()
        if products:
            lines = "\n".join(f"  • {p.name}: {p.quantity} {p.unit or 'pcs'}" for p in products)
            return f"⚠️ **Low Stock Products ({len(products)})**\n{lines}\n\nConsider restocking these items."
        return "✅ All products have sufficient stock (above 10 units)."

    # ── OUT OF STOCK ──────────────────────────────────────────────────────
    if _has("out of stock", "zero stock", "finished", "sesh"):
        products = db.session.execute(
            db.select(Product).where(Product.quantity == 0).order_by(Product.name)
        ).scalars().all()
        if products:
            names = "\n".join(f"  • {p.name}" for p in products)
            return f"🚨 **Out of Stock ({len(products)} products)**\n{names}"
        return "✅ No products are out of stock."

    # ── DEAD STOCK ────────────────────────────────────────────────────────
    if _has("dead stock", "not selling", "slow stock", "bikena"):
        dead = detect_dead_stock(days=30)
        if dead:
            lines = "\n".join(f"  • {d['product'].name} ({d['product'].quantity} in stock)" for d in dead[:5])
            return f"💤 **Dead Stock — No Sales in 30 Days ({len(dead)} products)**\n{lines}"
        return "✅ No dead stock. All products sold in the last 30 days."

    # ── STOCK OF SPECIFIC PRODUCT ─────────────────────────────────────────
    if "stock of" in msg or "stock" in msg:
        products = db.session.execute(db.select(Product)).scalars().all()
        for p in products:
            if p.name.lower() in msg:
                return (f"📦 **{p.name}**\n"
                        f"Stock: {p.quantity} {p.unit or 'pcs'}\n"
                        f"Price: NPR {float(p.selling_price):,.2f}\n"
                        f"SKU: {p.sku}")

    # ── TOTAL PRODUCTS ────────────────────────────────────────────────────
    if _has("how many product", "total product", "product count", "kitna product"):
        count = db.session.execute(db.select(func.count(Product.id))).scalar() or 0
        low = db.session.execute(db.select(func.count(Product.id)).where(Product.quantity <= 10)).scalar() or 0
        zero = db.session.execute(db.select(func.count(Product.id)).where(Product.quantity == 0)).scalar() or 0
        return f"📦 **Inventory Summary**\nTotal products: {count}\nLow stock: {low}\nOut of stock: {zero}"

    # ── TOP CUSTOMERS ─────────────────────────────────────────────────────
    if _has("top customer", "best customer", "loyal customer", "vip"):
        from ..models.customer import Customer
        rows = db.session.execute(
            db.select(Customer).order_by(Customer.visit_count.desc()).limit(5)
        ).scalars().all()
        if rows:
            lines = "\n".join(f"  {i+1}. {c.name} — {c.visit_count} visits" for i, c in enumerate(rows))
            return f"👥 **Top 5 Customers by Visits**\n{lines}"
        return "No customer data yet."

    if _has("how many customer", "total customer", "customer count"):
        from ..models.customer import Customer
        count = db.session.execute(db.select(func.count(Customer.id))).scalar() or 0
        return f"👥 You have **{count} registered customers**."

    # ── CREDIT / UDHARO ───────────────────────────────────────────────────
    if _has("credit", "udharo", "udhar", "outstanding", "due payment"):
        outstanding = db.session.execute(
            db.select(func.coalesce(func.sum(Sale.total_amount), 0))
            .where(Sale.payment_mode == "credit", Sale.credit_collected == False)
        ).scalar() or 0
        count = db.session.execute(
            db.select(func.count(Sale.id))
            .where(Sale.payment_mode == "credit", Sale.credit_collected == False)
        ).scalar() or 0
        overdue = db.session.execute(
            db.select(func.count(Sale.id))
            .where(Sale.payment_mode == "credit", Sale.credit_collected == False,
                   Sale.credit_due_date < today)
        ).scalar() or 0
        return (f"💳 **Credit / Udharo Summary**\n"
                f"Total outstanding: NPR {float(outstanding):,.2f}\n"
                f"Open credit sales: {count}\n"
                f"Overdue: {overdue}")

    # ── LOYALTY POINTS ────────────────────────────────────────────────────
    if _has("loyalty", "points", "reward"):
        from ..models.ai_enhancements import LoyaltyWallet
        total_pts = db.session.execute(
            db.select(func.coalesce(func.sum(LoyaltyWallet.points_balance), 0))
        ).scalar() or 0
        members = db.session.execute(
            db.select(func.count(LoyaltyWallet.id)).where(LoyaltyWallet.points_balance > 0)
        ).scalar() or 0
        return (f"⭐ **Loyalty Programme**\n"
                f"Active members: {members}\n"
                f"Total points outstanding: {int(total_pts):,} pts")

    # ── ALERTS ────────────────────────────────────────────────────────────
    if _has("alert", "attention", "problem", "issue", "warning", "urgent"):
        alerts = []
        low = db.session.execute(db.select(func.count(Product.id)).where(Product.quantity <= 10)).scalar() or 0
        zero = db.session.execute(db.select(func.count(Product.id)).where(Product.quantity == 0)).scalar() or 0
        overdue = db.session.execute(
            db.select(func.count(Sale.id))
            .where(Sale.payment_mode == "credit", Sale.credit_collected == False,
                   Sale.credit_due_date < today)
        ).scalar() or 0
        if zero: alerts.append(f"🚨 {zero} product(s) out of stock")
        if low: alerts.append(f"⚠️ {low} product(s) low on stock")
        if overdue: alerts.append(f"💳 {overdue} overdue credit payment(s)")
        if alerts:
            return "🔔 **Needs Attention:**\n" + "\n".join(alerts)
        return "✅ Everything looks good! No urgent alerts."

    # ── PURCHASES ─────────────────────────────────────────────────────────
    if _has("purchase", "kharida") or "supplier" in msg:
        from ..models.purchase import Purchase
        count = db.session.execute(
            db.select(func.count(Purchase.id))
            .where(func.date(Purchase.purchase_date) >= month_start)
        ).scalar() or 0
        total = db.session.execute(
            db.select(func.coalesce(func.sum(Purchase.total_cost), 0))
            .where(func.date(Purchase.purchase_date) >= month_start)
        ).scalar() or 0
        return f"🛒 **This Month's Purchases**\nOrders: {count}\nTotal cost: NPR {float(total):,.2f}"

    # ── FALLBACK ──────────────────────────────────────────────────────────
    return ("🤔 I didn't quite understand that.\n\n"
            "Try asking:\n"
            "• *'today sales'*\n"
            "• *'low stock'*\n"
            "• *'profit this month'*\n"
            "• *'top product'*\n"
            "• *'credit outstanding'*\n"
            "• *'forecast'*\n\n"
            "Or type **help** for all commands.")

    # ── Help ──────────────────────────────────────────────────────────────
    if _has("help", "what can you", "commands", "what do you know"):
        return ("🤖 **I can answer questions about:**\n\n"
                "📊 **Sales:** today, yesterday, this week, this month, last month\n"
                "💰 **Finance:** profit, expenses, cash balance\n"
                "📦 **Inventory:** low stock, dead stock, stock of [product]\n"
                "👥 **Customers:** top customers, credit/udharo, loyalty points\n"
                "🏆 **Products:** top seller, best product, least selling\n"
                "🔮 **Forecast:** tomorrow, next week prediction\n"
                "⚠️ **Alerts:** what needs attention\n\n"
                "Try: *'today sales'*, *'low stock'*, *'top customer'*, *'profit this month'*")

    # ── Today's sales ─────────────────────────────────────────────────────
    if _has("today", "aaj") and _has("sale", "revenue", "income", "earn", "becha"):
        total = _rev(today)
        count = _cnt(today)
        profit_today = 0
        try:
            cogs = db.session.execute(
                db.select(func.coalesce(func.sum(Product.cost_price * SaleItem.quantity), 0))
                .join(SaleItem, SaleItem.product_id == Product.id)
                .join(Sale, Sale.id == SaleItem.sale_id)
                .where(func.date(Sale.sale_date) == today)
            ).scalar() or 0
            profit_today = total - float(cogs)
        except Exception:
            pass
        if total == 0:
            return f"📊 No sales recorded today yet.\n\nYesterday's sales: NPR {_rev(yesterday, yesterday):,.0f}"
        return (f"📊 **Today's Sales**\n"
                f"Revenue: NPR {total:,.2f}\n"
                f"Transactions: {count}\n"
                f"Avg order: NPR {total/count:,.0f}\n"
                f"Est. profit: NPR {profit_today:,.0f}")

    # ── Yesterday ─────────────────────────────────────────────────────────
    if _has("yesterday", "kal"):
        total = _rev(yesterday, yesterday)
        count = _cnt(yesterday, yesterday)
        return f"📅 **Yesterday's Sales**\nRevenue: NPR {total:,.2f} from {count} transaction(s)."

    # ── Weekly ────────────────────────────────────────────────────────────
    if _has("this week", "weekly", "week sale", "hapta"):
        total = _rev(week_start)
        count = _cnt(week_start)
        daily_avg = total / max((today - week_start).days + 1, 1)
        return (f"📅 **This Week's Sales**\n"
                f"Revenue: NPR {total:,.2f}\n"
                f"Transactions: {count}\n"
                f"Daily avg: NPR {daily_avg:,.0f}")

    # ── Monthly ───────────────────────────────────────────────────────────
    if _has("this month", "monthly", "month sale", "mahina"):
        total = _rev(month_start)
        count = _cnt(month_start)
        days_passed = (today - month_start).days + 1
        projected = total / days_passed * 30
        return (f"📆 **This Month's Sales**\n"
                f"Revenue: NPR {total:,.2f}\n"
                f"Transactions: {count}\n"
                f"Projected month-end: NPR {projected:,.0f}")

    # ── Last month ────────────────────────────────────────────────────────
    if _has("last month", "pichlo mahina"):
        prev_end = month_start - timedelta(days=1)
        prev_start = prev_end.replace(day=1)
        total = _rev(prev_start, prev_end)
        count = _cnt(prev_start, prev_end)
        return f"📆 **Last Month's Sales**\nRevenue: NPR {total:,.2f} from {count} transaction(s)."

    # ── Profit ────────────────────────────────────────────────────────────
    if _has("profit", "earning", "nafa", "margin"):
        try:
            from . import cash_flow_manager
            pl = cash_flow_manager.profit_loss(month_start, today)
            rev = float(pl.get('revenue', 0))
            profit = float(pl.get('profit', 0))
            exp = float(pl.get('expenses', 0))
            margin = (profit / rev * 100) if rev > 0 else 0
            return (f"💰 **This Month's Profit**\n"
                    f"Revenue: NPR {rev:,.2f}\n"
                    f"Expenses: NPR {exp:,.2f}\n"
                    f"Net Profit: NPR {profit:,.2f}\n"
                    f"Margin: {margin:.1f}%")
        except Exception:
            return "💰 Profit data unavailable right now."

    # ── Expenses ──────────────────────────────────────────────────────────
    if _has("expense", "kharcha", "cost", "spending"):
        from ..models.expense import Expense
        total_exp = db.session.execute(
            db.select(func.coalesce(func.sum(Expense.amount), 0))
            .where(Expense.expense_date >= month_start)
        ).scalar() or 0
        by_type = db.session.execute(
            db.select(Expense.expense_type, func.sum(Expense.amount).label("total"))
            .where(Expense.expense_date >= month_start)
            .group_by(Expense.expense_type)
            .order_by(func.sum(Expense.amount).desc())
        ).all()
        breakdown = "\n".join(f"  • {r.expense_type}: NPR {float(r.total):,.0f}" for r in by_type)
        return f"💸 **This Month's Expenses**\nTotal: NPR {float(total_exp):,.2f}\n\n{breakdown}"

    # ── Cash balance ──────────────────────────────────────────────────────
    if _has("cash balance", "cash", "balance", "kitna paisa"):
        try:
            from . import cash_flow_manager
            balance = cash_flow_manager.daily_balance(today)
            return f"💵 **Today's Cash Balance:** NPR {float(balance):,.2f}"
        except Exception:
            return "💵 Cash balance unavailable."

    # ── Top products ──────────────────────────────────────────────────────
    if _has("top product", "best seller", "best selling", "most sold", "popular"):
        rows = db.session.execute(
            db.select(Product, func.sum(SaleItem.quantity).label("qty"),
                      func.sum(SaleItem.subtotal).label("rev"))
            .join(SaleItem, SaleItem.product_id == Product.id)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(func.date(Sale.sale_date) >= month_start)
            .group_by(Product.id)
            .order_by(func.sum(SaleItem.quantity).desc())
            .limit(5)
        ).all()
        if rows:
            lines = "\n".join(f"  {i+1}. {r.Product.name} — {r.qty} units (NPR {float(r.rev):,.0f})" for i, r in enumerate(rows))
            return f"🏆 **Top 5 Products This Month**\n{lines}"
        return "No sales data this month yet."

    # ── Least selling ─────────────────────────────────────────────────────
    if _has("least", "slow", "worst", "not selling"):
        rows = db.session.execute(
            db.select(Product, func.sum(SaleItem.quantity).label("qty"))
            .join(SaleItem, SaleItem.product_id == Product.id)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(func.date(Sale.sale_date) >= month_start)
            .group_by(Product.id)
            .order_by(func.sum(SaleItem.quantity).asc())
            .limit(5)
        ).all()
        if rows:
            lines = "\n".join(f"  {i+1}. {r.Product.name} — {r.qty} units" for i, r in enumerate(rows))
            return f"📉 **Least Selling Products This Month**\n{lines}"
        return "No sales data this month yet."

    # ── Low stock ─────────────────────────────────────────────────────────
    if _has("low stock", "stock low", "running out", "restock", "kam stock"):
        products = db.session.execute(
            db.select(Product).where(Product.quantity <= 10).order_by(Product.quantity).limit(8)
        ).scalars().all()
        if products:
            lines = "\n".join(f"  • {p.name}: {p.quantity} {p.unit or 'pcs'}" for p in products)
            return f"⚠️ **Low Stock Products ({len(products)})**\n{lines}\n\nConsider restocking these items."
        return "✅ All products have sufficient stock (above 10 units)."

    # ── Out of stock ──────────────────────────────────────────────────────
    if _has("out of stock", "zero stock", "finished", "sesh"):
        products = db.session.execute(
            db.select(Product).where(Product.quantity == 0).order_by(Product.name)
        ).scalars().all()
        if products:
            names = "\n".join(f"  • {p.name}" for p in products)
            return f"🚨 **Out of Stock ({len(products)} products)**\n{names}"
        return "✅ No products are out of stock."

    # ── Dead stock ────────────────────────────────────────────────────────
    if _has("dead stock", "not selling", "slow stock", "no sale", "bikena"):
        dead = detect_dead_stock(days=30)
        if dead:
            lines = "\n".join(f"  • {d['product'].name} ({d['product'].quantity} in stock)" for d in dead[:5])
            return f"💤 **Dead Stock — No Sales in 30 Days ({len(dead)} products)**\n{lines}"
        return "✅ No dead stock. All products sold in the last 30 days."

    # ── Stock of specific product ─────────────────────────────────────────
    if _has("stock of", "how much", "kitna", "quantity of", "stock"):
        products = db.session.execute(db.select(Product)).scalars().all()
        for p in products:
            if p.name.lower() in msg:
                return (f"📦 **{p.name}**\n"
                        f"Stock: {p.quantity} {p.unit or 'pcs'}\n"
                        f"Price: NPR {float(p.selling_price):,.2f}\n"
                        f"SKU: {p.sku}")
        return "I couldn't find that product. Try: *'stock of Cashew Nuts'*"

    # ── Total products ────────────────────────────────────────────────────
    if _has("how many product", "total product", "product count", "kitna product"):
        count = db.session.execute(db.select(func.count(Product.id))).scalar() or 0
        low = db.session.execute(db.select(func.count(Product.id)).where(Product.quantity <= 10)).scalar() or 0
        zero = db.session.execute(db.select(func.count(Product.id)).where(Product.quantity == 0)).scalar() or 0
        return f"📦 **Inventory Summary**\nTotal products: {count}\nLow stock: {low}\nOut of stock: {zero}"

    # ── Customers ─────────────────────────────────────────────────────────
    if _has("top customer", "best customer", "loyal customer", "vip"):
        from ..models.customer import Customer
        rows = db.session.execute(
            db.select(Customer).order_by(Customer.visit_count.desc()).limit(5)
        ).scalars().all()
        if rows:
            lines = "\n".join(f"  {i+1}. {c.name} — {c.visit_count} visits" for i, c in enumerate(rows))
            return f"👥 **Top 5 Customers by Visits**\n{lines}"
        return "No customer data yet."

    if _has("how many customer", "total customer", "customer count"):
        from ..models.customer import Customer
        count = db.session.execute(db.select(func.count(Customer.id))).scalar() or 0
        return f"👥 You have **{count} registered customers**."

    # ── Credit / Udharo ───────────────────────────────────────────────────
    if _has("credit", "udharo", "udhar", "outstanding", "due"):
        outstanding = db.session.execute(
            db.select(func.coalesce(func.sum(Sale.total_amount), 0))
            .where(Sale.payment_mode == "credit", Sale.credit_collected == False)
        ).scalar() or 0
        count = db.session.execute(
            db.select(func.count(Sale.id))
            .where(Sale.payment_mode == "credit", Sale.credit_collected == False)
        ).scalar() or 0
        overdue = db.session.execute(
            db.select(func.count(Sale.id))
            .where(Sale.payment_mode == "credit", Sale.credit_collected == False,
                   Sale.credit_due_date < today)
        ).scalar() or 0
        return (f"💳 **Credit / Udharo Summary**\n"
                f"Total outstanding: NPR {float(outstanding):,.2f}\n"
                f"Open credit sales: {count}\n"
                f"Overdue: {overdue}")

    # ── Loyalty points ────────────────────────────────────────────────────
    if _has("loyalty", "points", "reward"):
        from ..models.ai_enhancements import LoyaltyWallet
        total_pts = db.session.execute(
            db.select(func.coalesce(func.sum(LoyaltyWallet.points_balance), 0))
        ).scalar() or 0
        members = db.session.execute(
            db.select(func.count(LoyaltyWallet.id)).where(LoyaltyWallet.points_balance > 0)
        ).scalar() or 0
        return (f"⭐ **Loyalty Programme**\n"
                f"Active members: {members}\n"
                f"Total points outstanding: {int(total_pts):,} pts")

    # ── Forecast ──────────────────────────────────────────────────────────
    if _has("forecast", "predict", "next week", "tomorrow", "bholi"):
        forecasts = forecast_sales(days_ahead=7)
        if forecasts:
            tomorrow = forecasts[0]
            week_total = sum(f["predicted_sales"] for f in forecasts)
            lines = "\n".join(f"  {f['day_name']}: NPR {f['predicted_sales']:,.0f}" for f in forecasts[:5])
            return (f"🔮 **Sales Forecast**\n"
                    f"Tomorrow ({tomorrow['day_name']}): NPR {tomorrow['predicted_sales']:,.0f}\n"
                    f"Next 7 days: NPR {week_total:,.0f}\n\n"
                    f"Daily breakdown:\n{lines}")
        return "🔮 Not enough data for forecast yet. Need at least 7 days of sales history."

    # ── Alerts / what needs attention ─────────────────────────────────────
    if _has("alert", "attention", "problem", "issue", "warning", "urgent"):
        alerts = []
        low = db.session.execute(db.select(func.count(Product.id)).where(Product.quantity <= 10)).scalar() or 0
        zero = db.session.execute(db.select(func.count(Product.id)).where(Product.quantity == 0)).scalar() or 0
        overdue = db.session.execute(
            db.select(func.count(Sale.id))
            .where(Sale.payment_mode == "credit", Sale.credit_collected == False,
                   Sale.credit_due_date < today)
        ).scalar() or 0
        if zero: alerts.append(f"🚨 {zero} product(s) out of stock")
        if low: alerts.append(f"⚠️ {low} product(s) low on stock")
        if overdue: alerts.append(f"💳 {overdue} overdue credit payment(s)")
        if alerts:
            return "🔔 **Needs Attention:**\n" + "\n".join(alerts)
        return "✅ Everything looks good! No urgent alerts."

    # ── Purchases ─────────────────────────────────────────────────────────
    if _has("purchase", "buy", "supplier", "kharida"):
        from ..models.purchase import Purchase
        count = db.session.execute(
            db.select(func.count(Purchase.id))
            .where(func.date(Purchase.purchase_date) >= month_start)
        ).scalar() or 0
        total = db.session.execute(
            db.select(func.coalesce(func.sum(Purchase.total_cost), 0))
            .where(func.date(Purchase.purchase_date) >= month_start)
        ).scalar() or 0
        return f"🛒 **This Month's Purchases**\nOrders: {count}\nTotal cost: NPR {float(total):,.2f}"

    # ── Help fallback ─────────────────────────────────────────────────────
    return ("🤔 I didn't quite understand that.\n\n"
            "Try asking:\n"
            "• *'today sales'*\n"
            "• *'low stock'*\n"
            "• *'profit this month'*\n"
            "• *'top product'*\n"
            "• *'credit outstanding'*\n"
            "• *'forecast'*\n\n"
            "Or type **help** for all commands.")
