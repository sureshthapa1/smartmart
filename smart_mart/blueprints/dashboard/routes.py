"""Dashboard blueprint — key metrics and charts."""

from datetime import date, timedelta

from flask import Blueprint, jsonify, render_template
from sqlalchemy import func

from ...extensions import db
from ...models.product import Product
from ...models.sale import Sale
from ...services import alert_engine, cash_flow_manager
from ...services.decorators import login_required

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@dashboard_bp.route("/")
@login_required
def index():
    total_products = db.session.execute(db.select(func.count(Product.id))).scalar() or 0
    stock_value = db.session.execute(
        db.select(func.coalesce(func.sum(Product.cost_price * Product.quantity), 0))
    ).scalar() or 0
    today_sales = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) == date.today())
    ).scalar() or 0
    month_start = date.today().replace(day=1)
    monthly_profit = cash_flow_manager.profit_loss(month_start, date.today())["profit"]
    alerts = alert_engine.get_all_alerts()
    low_stock = alerts["low_stock"]
    return render_template("dashboard/index.html",
                           total_products=total_products,
                           stock_value=float(stock_value),
                           today_sales=float(today_sales),
                           monthly_profit=float(monthly_profit),
                           low_stock=low_stock,
                           alert_counts={
                               "low_stock": len(alerts["low_stock"]),
                               "expiry": len(alerts["expiry"]),
                               "high_demand": len(alerts["high_demand"]),
                           })
