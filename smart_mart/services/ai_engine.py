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
    """Rule-based chatbot for querying sales and inventory."""
    msg = message.lower().strip()
    today = date.today()
    month_start = today.replace(day=1)
    week_start = today - timedelta(days=today.weekday())

    # ── Today's sales ─────────────────────────────────────────────────────
    if any(w in msg for w in ["today sale", "today's sale", "aaj ko sale", "today revenue"]):
        total = db.session.execute(
            db.select(func.coalesce(func.sum(Sale.total_amount), 0))
            .where(func.date(Sale.sale_date) == today)
        ).scalar() or 0
        count = db.session.execute(
            db.select(func.count(Sale.id))
            .where(func.date(Sale.sale_date) == today)
        ).scalar() or 0
        return f"📊 Today's sales: NPR {float(total):,.2f} from {count} transaction(s)."

    # ── Weekly sales ──────────────────────────────────────────────────────
    if any(w in msg for w in ["this week", "weekly sale", "week sale"]):
        total = db.session.execute(
            db.select(func.coalesce(func.sum(Sale.total_amount), 0))
            .where(func.date(Sale.sale_date) >= week_start)
        ).scalar() or 0
        return f"📅 This week's sales: NPR {float(total):,.2f}."

    # ── Monthly sales ─────────────────────────────────────────────────────
    if any(w in msg for w in ["this month", "monthly sale", "month sale"]):
        total = db.session.execute(
            db.select(func.coalesce(func.sum(Sale.total_amount), 0))
            .where(func.date(Sale.sale_date) >= month_start)
        ).scalar() or 0
        return f"📆 This month's sales: NPR {float(total):,.2f}."

    # ── Top product ───────────────────────────────────────────────────────
    if any(w in msg for w in ["top product", "best seller", "best selling", "most sold"]):
        row = db.session.execute(
            db.select(Product, func.sum(SaleItem.quantity).label("qty"))
            .join(SaleItem, SaleItem.product_id == Product.id)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(func.date(Sale.sale_date) >= month_start)
            .group_by(Product.id)
            .order_by(func.sum(SaleItem.quantity).desc())
            .limit(1)
        ).first()
        if row:
            return f"🏆 Top selling product this month: {row.Product.name} ({row.qty} units sold)."
        return "No sales data available for this month."

    # ── Low stock ─────────────────────────────────────────────────────────
    if any(w in msg for w in ["low stock", "stock low", "running out", "restock"]):
        products = db.session.execute(
            db.select(Product).where(Product.quantity <= 10).order_by(Product.quantity).limit(5)
        ).scalars().all()
        if products:
            names = ", ".join(f"{p.name} ({p.quantity})" for p in products)
            return f"⚠️ Low stock products: {names}."
        return "✅ All products have sufficient stock."

    # ── Total products ────────────────────────────────────────────────────
    if any(w in msg for w in ["how many product", "total product", "product count"]):
        count = db.session.execute(db.select(func.count(Product.id))).scalar() or 0
        return f"📦 You have {count} products in inventory."

    # ── Stock of specific product ─────────────────────────────────────────
    if "stock of" in msg or "how much" in msg:
        # Try to find product name in message
        products = db.session.execute(db.select(Product)).scalars().all()
        for p in products:
            if p.name.lower() in msg:
                return f"📦 {p.name}: {p.quantity} {p.unit or 'pcs'} in stock."
        return "I couldn't find that product. Try: 'stock of Rice' or 'stock of Milk'."

    # ── Forecast ──────────────────────────────────────────────────────────
    if any(w in msg for w in ["forecast", "predict", "next week", "tomorrow sale"]):
        forecasts = forecast_sales(days_ahead=7)
        total_forecast = sum(f["predicted_sales"] for f in forecasts)
        next_day = forecasts[0]
        return (f"🔮 Sales forecast: Tomorrow ({next_day['day_name']}) ≈ NPR {next_day['predicted_sales']:,.0f}. "
                f"Next 7 days total ≈ NPR {total_forecast:,.0f}.")

    # ── Profit ────────────────────────────────────────────────────────────
    if any(w in msg for w in ["profit", "earning", "income"]):
        from . import cash_flow_manager
        pl = cash_flow_manager.profit_loss(month_start, today)
        return (f"💰 This month: Revenue NPR {float(pl['revenue']):,.2f}, "
                f"Profit NPR {float(pl['profit']):,.2f}.")

    # ── Dead stock ────────────────────────────────────────────────────────
    if any(w in msg for w in ["dead stock", "not selling", "slow stock", "no sale"]):
        dead = detect_dead_stock(days=30)
        if dead:
            names = ", ".join(d["product"].name for d in dead[:3])
            return f"💤 Dead stock (no sales in 30d): {names}{'...' if len(dead) > 3 else ''}. Total: {len(dead)} products."
        return "✅ No dead stock detected. All products sold in last 30 days."

    # ── Help ──────────────────────────────────────────────────────────────
    if any(w in msg for w in ["help", "what can you", "commands", "?"]):
        return ("🤖 I can answer:\n"
                "• Today's / weekly / monthly sales\n"
                "• Top product / best seller\n"
                "• Low stock / restock alerts\n"
                "• Stock of [product name]\n"
                "• Sales forecast / predict\n"
                "• Profit / earnings\n"
                "• Dead stock / slow stock\n"
                "• How many products")

    return ("🤔 I didn't understand that. Try asking about:\n"
            "sales, stock, profit, forecast, top product, or type 'help'.")
