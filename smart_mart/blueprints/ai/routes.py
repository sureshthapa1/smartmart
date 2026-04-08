"""AI blueprint — demand prediction, insights, forecasting, chatbot, and advanced AI modules."""

from flask import Blueprint, jsonify, render_template, request
from ...services import ai_engine
from ...services.ai_trend_analyzer import (
    fast_moving_products, dead_stock_analysis,
    seasonal_demand_patterns, trend_dashboard
)
from ...services.ai_invoice_detector import validate_sale_items
from ...services.ai_profit_leak import profit_leak_dashboard, low_margin_products, discount_loss_analysis
from ...services.ai_supplier_scorer import supplier_scorecard_all, score_supplier
from ...services.ai_simulation import (
    simulate_sales_change, simulate_price_change,
    simulate_expense_change, simulate_stock_out
)
from ...services.decorators import admin_required, login_required
from ...extensions import db
from ...models.product import Product

ai_bp = Blueprint("ai", __name__, url_prefix="/ai")


@ai_bp.route("/insights")
@admin_required
def insights():
    """AI-generated business insights page."""
    insights_data = ai_engine.generate_insights()
    forecasts = ai_engine.forecast_sales(days_ahead=7)
    dead_stock = ai_engine.detect_dead_stock(days=30)

    # Restock recommendations for low-stock products
    low_stock = db.session.execute(
        db.select(Product).where(Product.quantity <= 10).order_by(Product.quantity)
    ).scalars().all()
    restock_recs = []
    for p in low_stock[:10]:
        rec = ai_engine.restock_recommendation(p.id)
        rec["product"] = p
        restock_recs.append(rec)

    return render_template("ai/insights.html",
                           insights=insights_data,
                           forecasts=forecasts,
                           dead_stock=dead_stock,
                           restock_recs=restock_recs)


@ai_bp.route("/product/<int:product_id>")
@admin_required
def product_analysis(product_id):
    """Detailed AI analysis for a single product."""
    product = db.get_or_404(Product, product_id)
    demand = ai_engine.demand_prediction(product_id)
    restock = ai_engine.restock_recommendation(product_id)
    forecast = ai_engine.forecast_product_demand(product_id, days_ahead=7)
    return render_template("ai/product_analysis.html",
                           product=product, demand=demand,
                           restock=restock, forecast=forecast)


@ai_bp.route("/chatbot", methods=["GET"])
@login_required
def chatbot():
    return render_template("ai/chatbot.html")


@ai_bp.route("/chatbot/query", methods=["POST"])
@login_required
def chatbot_query():
    data = request.get_json() or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"reply": "Please type a message."})
    reply = ai_engine.chatbot_query(message)
    return jsonify({"reply": reply})


# ── JSON API endpoints for charts ─────────────────────────────────────────────

@ai_bp.route("/api/forecast")
@admin_required
def api_forecast():
    forecasts = ai_engine.forecast_sales(days_ahead=7)
    return jsonify({
        "labels": [f["day_name"] for f in forecasts],
        "predicted": [f["predicted_sales"] for f in forecasts],
        "low": [f["confidence_low"] for f in forecasts],
        "high": [f["confidence_high"] for f in forecasts],
    })


@ai_bp.route("/api/demand/<int:product_id>")
@admin_required
def api_demand(product_id):
    forecast = ai_engine.forecast_product_demand(product_id, days_ahead=7)
    demand = ai_engine.demand_prediction(product_id)
    return jsonify({
        "labels": [f["day_name"] for f in forecast],
        "predicted": [f["predicted_qty"] for f in forecast],
        "demand": demand,
    })


# ═══════════════════════════════════════════════════════════════════════════
# MODULE 1: TREND ANALYZER APIs
# ═══════════════════════════════════════════════════════════════════════════

@ai_bp.route("/api/trends/fast-moving")
@admin_required
def api_fast_moving():
    """GET /ai/api/trends/fast-moving?period=weekly&top=10"""
    period = request.args.get("period", "weekly")
    top = int(request.args.get("top", 10))
    return jsonify(fast_moving_products(period, top))


@ai_bp.route("/api/trends/dead-stock")
@admin_required
def api_dead_stock():
    """GET /ai/api/trends/dead-stock?days=30"""
    days = int(request.args.get("days", 30))
    return jsonify(dead_stock_analysis(days))


@ai_bp.route("/api/trends/seasonal")
@admin_required
def api_seasonal():
    """GET /ai/api/trends/seasonal?product_id=1"""
    product_id = request.args.get("product_id", type=int)
    return jsonify(seasonal_demand_patterns(product_id))


@ai_bp.route("/api/trends/dashboard")
@admin_required
def api_trend_dashboard():
    """GET /ai/api/trends/dashboard — full trend insights"""
    return jsonify(trend_dashboard())


# ═══════════════════════════════════════════════════════════════════════════
# MODULE 2: INVOICE ERROR DETECTION APIs
# ═══════════════════════════════════════════════════════════════════════════

@ai_bp.route("/api/invoice/validate", methods=["POST"])
@login_required
def api_validate_invoice():
    """POST /ai/api/invoice/validate
    Body: {"items": [...], "discount_amount": 0}
    """
    data = request.get_json() or {}
    items = data.get("items", [])
    discount = float(data.get("discount_amount", 0))
    result = validate_sale_items(items, discount)
    return jsonify(result)


# ═══════════════════════════════════════════════════════════════════════════
# MODULE 3: PROFIT LEAK DETECTION APIs
# ═══════════════════════════════════════════════════════════════════════════

@ai_bp.route("/api/profit-leak/dashboard")
@admin_required
def api_profit_leak():
    """GET /ai/api/profit-leak/dashboard"""
    return jsonify(profit_leak_dashboard())


@ai_bp.route("/api/profit-leak/low-margin")
@admin_required
def api_low_margin():
    """GET /ai/api/profit-leak/low-margin?threshold=15"""
    threshold = float(request.args.get("threshold", 15.0))
    return jsonify(low_margin_products(threshold))


@ai_bp.route("/api/profit-leak/discounts")
@admin_required
def api_discount_losses():
    """GET /ai/api/profit-leak/discounts?days=30"""
    days = int(request.args.get("days", 30))
    return jsonify(discount_loss_analysis(days))


# ═══════════════════════════════════════════════════════════════════════════
# MODULE 4: SUPPLIER SCORING APIs
# ═══════════════════════════════════════════════════════════════════════════

@ai_bp.route("/api/suppliers/scorecard")
@admin_required
def api_supplier_scorecard():
    """GET /ai/api/suppliers/scorecard — all suppliers ranked"""
    return jsonify(supplier_scorecard_all())


@ai_bp.route("/api/suppliers/<int:supplier_id>/score")
@admin_required
def api_supplier_score(supplier_id):
    """GET /ai/api/suppliers/1/score"""
    return jsonify(score_supplier(supplier_id))


# ═══════════════════════════════════════════════════════════════════════════
# MODULE 5: SIMULATION APIs
# ═══════════════════════════════════════════════════════════════════════════

@ai_bp.route("/api/simulate/sales", methods=["POST"])
@admin_required
def api_simulate_sales():
    """POST /ai/api/simulate/sales
    Body: {"change_pct": 20, "days": 30}
    """
    data = request.get_json() or {}
    change_pct = float(data.get("change_pct", 10))
    days = int(data.get("days", 30))
    return jsonify(simulate_sales_change(change_pct, days))


@ai_bp.route("/api/simulate/price", methods=["POST"])
@admin_required
def api_simulate_price():
    """POST /ai/api/simulate/price
    Body: {"product_id": 1, "new_price": 150.0, "days": 30}
    """
    data = request.get_json() or {}
    product_id = int(data.get("product_id", 0))
    new_price = float(data.get("new_price", 0))
    days = int(data.get("days", 30))
    return jsonify(simulate_price_change(product_id, new_price, days))


@ai_bp.route("/api/simulate/expenses", methods=["POST"])
@admin_required
def api_simulate_expenses():
    """POST /ai/api/simulate/expenses
    Body: {"change_pct": -10, "days": 30}
    """
    data = request.get_json() or {}
    change_pct = float(data.get("change_pct", -10))
    days = int(data.get("days", 30))
    return jsonify(simulate_expense_change(change_pct, days))


@ai_bp.route("/api/simulate/stockout/<int:product_id>")
@admin_required
def api_simulate_stockout(product_id):
    """GET /ai/api/simulate/stockout/1"""
    days = int(request.args.get("days", 30))
    return jsonify(simulate_stock_out(product_id, days))


# ═══════════════════════════════════════════════════════════════════════════
# ADVANCED AI DASHBOARD PAGE
# ═══════════════════════════════════════════════════════════════════════════

@ai_bp.route("/advanced")
@admin_required
def advanced_dashboard():
    """Full advanced AI dashboard."""
    from ...models.supplier import Supplier
    suppliers = db.session.execute(db.select(Supplier).order_by(Supplier.name)).scalars().all()
    products = db.session.execute(db.select(Product).order_by(Product.name)).scalars().all()
    return render_template("ai/advanced_dashboard.html",
                           suppliers=suppliers, products=products)


# ═══════════════════════════════════════════════════════════════════════════
# FEATURE 2: CUSTOMER SEGMENTATION
# ═══════════════════════════════════════════════════════════════════════════

@ai_bp.route("/api/customers/segments")
@admin_required
def api_customer_segments():
    from ...services.ai_customer_segmentation import segment_customers
    return jsonify(segment_customers())


@ai_bp.route("/api/customers/profile")
@admin_required
def api_customer_profile():
    name = request.args.get("name", "")
    from ...services.ai_customer_segmentation import get_customer_profile
    return jsonify(get_customer_profile(name))



# ═══════════════════════════════════════════════════════════════════════════
# FEATURE 3: ANOMALY DETECTION
# ═══════════════════════════════════════════════════════════════════════════

@ai_bp.route("/api/anomalies")
@admin_required
def api_anomalies():
    days = int(request.args.get("days", 30))
    from ...services.ai_anomaly_detection import full_anomaly_report
    return jsonify(full_anomaly_report(days))


@ai_bp.route("/anomalies")
@admin_required
def anomalies_page():
    from ...services.ai_anomaly_detection import full_anomaly_report
    report = full_anomaly_report(30)
    return render_template("ai/anomalies.html", report=report)


# ═══════════════════════════════════════════════════════════════════════════
# FEATURE 5: NATURAL LANGUAGE REPORTS
# ═══════════════════════════════════════════════════════════════════════════

@ai_bp.route("/api/nlg/daily")
@login_required
def api_nlg_daily():
    from ...services.ai_nlg import generate_daily_report
    return jsonify(generate_daily_report())


@ai_bp.route("/api/nlg/weekly")
@login_required
def api_nlg_weekly():
    from ...services.ai_nlg import generate_weekly_report
    return jsonify(generate_weekly_report())


@ai_bp.route("/api/nlg/monthly")
@admin_required
def api_nlg_monthly():
    from ...services.ai_nlg import generate_monthly_report
    return jsonify(generate_monthly_report())


@ai_bp.route("/api/nlg/summary")
@login_required
def api_nlg_summary():
    from ...services.ai_nlg import generate_smart_summary
    return jsonify(generate_smart_summary())


# ═══════════════════════════════════════════════════════════════════════════
# FEATURE 6: IMAGE RECOGNITION
# ═══════════════════════════════════════════════════════════════════════════

@ai_bp.route("/api/image/recognize", methods=["POST"])
@login_required
def api_image_recognize():
    """POST with multipart/form-data file OR JSON {"filename": "..."}"""
    from ...services.ai_image_recognition import recognize_from_filename, recognize_from_text
    import os

    # File upload
    if "file" in request.files:
        f = request.files["file"]
        result = recognize_from_filename(f.filename)
        # Save the image if it's a product image
        if f and f.filename:
            import uuid
            ext = os.path.splitext(f.filename)[1].lower()
            filename = f"{uuid.uuid4().hex}{ext}"
            upload_dir = os.path.join(db.get_app().static_folder, "uploads", "products")
            os.makedirs(upload_dir, exist_ok=True)
            f.save(os.path.join(upload_dir, filename))
            result["saved_filename"] = filename
        return jsonify(result)

    # Text/filename input
    data = request.get_json() or {}
    text = data.get("filename") or data.get("text") or data.get("name") or ""
    return jsonify(recognize_from_text(text))


# ═══════════════════════════════════════════════════════════════════════════
# FEATURE 7: VOICE ASSISTANT
# ═══════════════════════════════════════════════════════════════════════════

@ai_bp.route("/api/voice/query", methods=["POST"])
@login_required
def api_voice_query():
    """Process voice transcript."""
    data = request.get_json() or {}
    transcript = data.get("transcript", "")
    from ...services.ai_voice import process_voice_command
    return jsonify(process_voice_command(transcript))


@ai_bp.route("/voice")
@login_required
def voice_assistant():
    return render_template("ai/voice_assistant.html")


# ═══════════════════════════════════════════════════════════════════════════
# FEATURE 9: CASH FLOW PREDICTION
# ═══════════════════════════════════════════════════════════════════════════

@ai_bp.route("/api/cashflow/predict")
@admin_required
def api_cashflow_predict():
    days = int(request.args.get("days", 30))
    from ...services.ai_cashflow_prediction import predict_cashflow
    return jsonify(predict_cashflow(days))


@ai_bp.route("/cashflow")
@admin_required
def cashflow_page():
    from ...services.ai_cashflow_prediction import predict_cashflow
    data = predict_cashflow(30)
    return render_template("ai/cashflow.html", data=data)


# ═══════════════════════════════════════════════════════════════════════════
# FEATURE 10: EXPENSE CATEGORIZATION
# ═══════════════════════════════════════════════════════════════════════════

@ai_bp.route("/api/expense/categorize", methods=["POST"])
@login_required
def api_expense_categorize():
    data = request.get_json() or {}
    description = data.get("description", "")
    amount = float(data.get("amount", 0))
    from ...services.ai_expense_categorizer import categorize_expense
    return jsonify(categorize_expense(description, amount))


@ai_bp.route("/api/expense/patterns")
@admin_required
def api_expense_patterns():
    days = int(request.args.get("days", 30))
    from ...services.ai_expense_categorizer import analyze_expense_patterns
    return jsonify(analyze_expense_patterns(days))


# ═══════════════════════════════════════════════════════════════════════════
# CUSTOMER INTELLIGENCE MODULE (Features 6-14)
# ═══════════════════════════════════════════════════════════════════════════

from ...services.ai_customer_intelligence import (
    tier_customers, customer_behavior, personalized_recommendations,
    churn_prediction, customer_lifetime_value, loyalty_offers,
    product_affinity_analysis, generate_combos, customer_profitability
)


@ai_bp.route("/customers")
@admin_required
def customer_intelligence():
    """Customer Intelligence Dashboard."""
    tiers = tier_customers()
    churn = churn_prediction()
    affinity = product_affinity_analysis()
    combos = generate_combos()
    profitability = customer_profitability()
    return render_template("ai/customer_intelligence.html",
                           tiers=tiers, churn=churn,
                           affinity=affinity, combos=combos,
                           profitability=profitability)


@ai_bp.route("/customers/<string:customer_name>")
@admin_required
def customer_profile(customer_name):
    """Individual customer deep analysis."""
    behavior = customer_behavior(customer_name)
    recs = personalized_recommendations(customer_name)
    clv = customer_lifetime_value(customer_name)
    offers = loyalty_offers(customer_name)
    return render_template("ai/customer_profile.html",
                           customer_name=customer_name,
                           behavior=behavior, recs=recs,
                           clv=clv, offers=offers)


# ── Customer Intelligence REST APIs ──────────────────────────────────────────

@ai_bp.route("/api/customers/tiers")
@admin_required
def api_customer_tiers():
    """GET /ai/api/customers/tiers"""
    return jsonify(tier_customers())


@ai_bp.route("/api/customers/<string:name>/behavior")
@admin_required
def api_customer_behavior(name):
    """GET /ai/api/customers/John/behavior"""
    return jsonify(customer_behavior(name))


@ai_bp.route("/api/customers/<string:name>/recommendations")
@admin_required
def api_customer_recommendations(name):
    """GET /ai/api/customers/John/recommendations"""
    return jsonify(personalized_recommendations(name))


@ai_bp.route("/api/customers/churn")
@admin_required
def api_churn():
    """GET /ai/api/customers/churn"""
    return jsonify(churn_prediction())


@ai_bp.route("/api/customers/<string:name>/clv")
@admin_required
def api_clv(name):
    """GET /ai/api/customers/John/clv"""
    return jsonify(customer_lifetime_value(name))


@ai_bp.route("/api/customers/<string:name>/offers")
@admin_required
def api_loyalty_offers(name):
    """GET /ai/api/customers/John/offers"""
    return jsonify(loyalty_offers(name))


@ai_bp.route("/api/products/affinity")
@admin_required
def api_affinity():
    """GET /ai/api/products/affinity"""
    return jsonify(product_affinity_analysis())


@ai_bp.route("/api/products/combos")
@admin_required
def api_combos():
    """GET /ai/api/products/combos"""
    return jsonify(generate_combos())


@ai_bp.route("/api/customers/profitability")
@admin_required
def api_customer_profitability():
    """GET /ai/api/customers/profitability"""
    return jsonify(customer_profitability())
