import json
import os
import urllib.error
import urllib.request
from datetime import date, timedelta

from flask import Blueprint, jsonify, render_template, request
from sqlalchemy import func

from ...extensions import db
from ...models.expense import Expense
from ...models.product import Product
from ...models.sale import Sale, SaleItem
from ...services.decorators import login_required
from ...utils.nepali_date import bs_month_name

ai_chat_bp = Blueprint("ai_chat", __name__, url_prefix="/ai/chat")

SYSTEM_PROMPT = """You are the GoldKernel Smart Business Advisor, an AI assistant built into the SmartMart
retail management system for GoldKernel Dry Fruits & Treats, a premium dry fruits shop
in Dhangadhi, Nepal.

Your role:
- Answer questions about sales performance, inventory, profit, and customer trends
- Give practical business advice for Nepal's retail market
- Reference the live shop data provided when answering data questions
- Mention Nepali festivals (Dashain, Tihar) and seasonal patterns when relevant
- Currency is NPR. Weights are in grams.
- Be concise, friendly, and action-oriented (3-5 sentences unless asked for more)
- Use bullet points when listing items

[LIVE DATA]
{injected_business_context}
[/LIVE DATA]
"""


@ai_chat_bp.route("/")
@login_required
def index():
    return render_template("ai_chat/index.html", api_key_configured=bool(os.environ.get("ANTHROPIC_API_KEY")))


@ai_chat_bp.route("/ask", methods=["POST"])
@login_required
def ask():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not configured. Add it in Render environment variables to enable the AI Advisor."}), 500

    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Message is required."}), 400
    history = payload.get("history") or []
    history = history[-10:]

    messages = []
    for item in history:
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    body = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1000,
        "system": SYSTEM_PROMPT.format(injected_business_context=build_business_context()),
        "messages": messages,
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        text_parts = [
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        ]
        return jsonify({"reply": "\n".join(text_parts).strip() or "I could not generate a reply."})
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        return jsonify({"error": f"Claude API error: {detail}"}), 502
    except Exception as exc:
        return jsonify({"error": f"AI Advisor is unavailable right now: {exc}"}), 502


def build_business_context():
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    today_row = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0), func.count(Sale.id))
        .where(func.date(Sale.sale_date) == today)
    ).one()
    week_revenue = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= week_start)
    ).scalar() or 0
    month_revenue = db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(func.date(Sale.sale_date) >= month_start)
    ).scalar() or 0
    month_expenses = db.session.execute(
        db.select(func.coalesce(func.sum(Expense.amount), 0))
        .where(Expense.expense_date >= month_start)
    ).scalar() or 0
    top_products = db.session.execute(
        db.select(
            Product.name,
            func.coalesce(func.sum(SaleItem.quantity), 0).label("qty"),
            func.coalesce(func.sum(SaleItem.subtotal), 0).label("revenue"),
        )
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(func.date(Sale.sale_date) >= month_start)
        .group_by(Product.name)
        .order_by(func.sum(SaleItem.quantity).desc())
        .limit(5)
    ).all()
    low_stock = db.session.execute(
        db.select(Product)
        .where(Product.is_active == True)
        .where(Product.quantity < 500)
        .order_by(Product.quantity.asc())
        .limit(10)
    ).scalars().all()
    customers = db.session.execute(
        db.select(Sale.customer_name, func.count(Sale.id).label("visits"), func.sum(Sale.total_amount).label("spent"))
        .where(Sale.customer_name.isnot(None))
        .where(func.date(Sale.sale_date) >= month_start)
        .group_by(Sale.customer_name)
        .order_by(func.count(Sale.id).desc())
        .limit(5)
    ).all()

    top_lines = [f"- {row.name}: {int(row.qty or 0)}g sold, NPR {float(row.revenue or 0):,.2f}" for row in top_products] or ["- No product sales yet this month."]
    low_lines = [f"- {p.name}: {p.quantity}g" for p in low_stock] or ["- No products under 500g."]
    customer_lines = [f"- {row.customer_name}: {row.visits} visits, NPR {float(row.spent or 0):,.2f}" for row in customers] or ["- No repeat customer data this month."]
    return "\n".join([
        f"Date: {today.isoformat()} AD; BS month: {bs_month_name(today)}",
        f"Today's revenue: NPR {float(today_row[0] or 0):,.2f}; sales count: {today_row[1] or 0}",
        f"This week's revenue: NPR {float(week_revenue):,.2f}",
        f"This month's revenue: NPR {float(month_revenue):,.2f}",
        f"This month's profit estimate: NPR {float(month_revenue) - float(month_expenses):,.2f}",
        "Top 5 selling products this month:",
        *top_lines,
        "Low stock alerts under 500g:",
        *low_lines,
        "Frequent customers this month:",
        *customer_lines,
    ])
