# smart_mart/blueprints/ai_chat/routes.py
# ==========================================
# AI Business Advisor chatbot — powered by Claude API.
# Staff can ask plain-English questions about sales, stock, profit, customers.

from flask import Blueprint, render_template, request, jsonify, stream_with_context, Response
from flask_login import login_required, current_user
from smart_mart.extensions import db
import os
import json
import datetime

ai_chat_bp = Blueprint(
    "ai_chat", __name__,
    url_prefix="/ai/chat",
    template_folder="../../templates/ai_chat",
)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-20250514"


def _get_business_context() -> str:
    """Pull live stats from DB to give Claude real data context."""
    try:
        from smart_mart.models.sale import Sale
        from smart_mart.models.product import Product
        from smart_mart.models.expense import Expense

        today      = datetime.date.today()
        month_start = today.replace(day=1)
        week_start  = today - datetime.timedelta(days=today.weekday())

        # Monthly revenue
        monthly_sales = db.session.execute(
            db.text("""
                SELECT COALESCE(SUM(total_amount), 0) as rev,
                       COUNT(*) as cnt
                FROM sales
                WHERE DATE(created_at) >= :start AND DATE(created_at) <= :end
            """),
            {"start": month_start, "end": today},
        ).fetchone()

        # Weekly revenue
        weekly_sales = db.session.execute(
            db.text("""
                SELECT COALESCE(SUM(total_amount), 0) as rev
                FROM sales
                WHERE DATE(created_at) >= :start
            """),
            {"start": week_start},
        ).fetchone()

        # Today revenue
        today_sales = db.session.execute(
            db.text("""
                SELECT COALESCE(SUM(total_amount), 0) as rev
                FROM sales WHERE DATE(created_at) = :today
            """),
            {"today": today},
        ).fetchone()

        # Top 5 products this month
        top_products = db.session.execute(
            db.text("""
                SELECT p.name, SUM(si.quantity) as qty, SUM(si.quantity * si.unit_price) as rev
                FROM sale_items si
                JOIN products p ON si.product_id = p.id
                JOIN sales s ON si.sale_id = s.id
                WHERE DATE(s.created_at) >= :start
                GROUP BY p.id, p.name
                ORDER BY rev DESC LIMIT 5
            """),
            {"start": month_start},
        ).fetchall()

        # Low stock (under 500g)
        low_stock = db.session.execute(
            db.text("""
                SELECT name, stock_quantity
                FROM products
                WHERE is_active = 1 AND stock_quantity < 500
                ORDER BY stock_quantity ASC LIMIT 10
            """),
        ).fetchall()

        # Monthly expenses
        monthly_expenses = db.session.execute(
            db.text("""
                SELECT COALESCE(SUM(amount), 0) FROM expenses
                WHERE DATE(created_at) >= :start
            """),
            {"start": month_start},
        ).fetchone()

        ctx = f"""
LIVE BUSINESS DATA (as of {today.strftime('%d %b %Y')}):

Revenue:
- Today: NPR {float(today_sales[0]):,.0f}
- This week: NPR {float(weekly_sales[0]):,.0f}
- This month: NPR {float(monthly_sales[0]):,.0f} ({monthly_sales[1]} sales)

Monthly expenses so far: NPR {float(monthly_expenses[0]):,.0f}
Monthly profit so far: NPR {float(monthly_sales[0]) - float(monthly_expenses[0]):,.0f}

Top selling products this month:
{chr(10).join(f"  - {r[0]}: {float(r[1]):.0f} units sold, NPR {float(r[2]):,.0f} revenue" for r in top_products)}

Low stock alerts:
{chr(10).join(f"  - {r[0]}: {float(r[1]):.0f}g remaining" for r in low_stock) or "  (all products well-stocked)"}
"""
        return ctx

    except Exception as e:
        return f"(Could not load live data: {e}. Answer based on general business principles.)"


SYSTEM_PROMPT = """You are the GoldKernel Smart Business Advisor — an AI assistant built into the SmartMart retail management system for GoldKernel Dry Fruits & Treats, a premium dry fruits shop in Dhangadhi, Nepal.

Your role:
- Answer questions about sales performance, inventory, profit, customer trends
- Give practical business advice relevant to Nepal retail and dry fruits
- Be concise, friendly, and action-oriented
- Always reference the live data provided when answering data questions
- Mention Nepali festivals (Dashain, Tihar, etc.) and seasonal patterns when relevant
- Currency is NPR (Nepali Rupees)
- Weights are in grams

Keep responses short (3-5 sentences max unless asked for details). Use bullet points when listing items. If you don't have enough data, say so honestly."""


@ai_chat_bp.route("/")
@login_required
def index():
    return render_template("ai_chat/index.html")


@ai_chat_bp.route("/ask", methods=["POST"])
@login_required
def ask():
    """Non-streaming chat endpoint. Returns JSON."""
    if not ANTHROPIC_API_KEY:
        return jsonify({
            "error": "ANTHROPIC_API_KEY not set. Add it in Render environment variables."
        }), 500

    data = request.get_json()
    user_message = (data.get("message") or "").strip()
    history       = data.get("history", [])   # [{role, content}]

    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    # Build messages list
    messages = []
    for h in history[-10:]:   # keep last 10 turns for context
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_message})

    # Inject live data into system prompt
    system = SYSTEM_PROMPT + "\n\n" + _get_business_context()

    import urllib.request
    req_body = json.dumps({
        "model":      CLAUDE_MODEL,
        "max_tokens": 1000,
        "system":     system,
        "messages":   messages,
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data    = req_body,
        headers = {
            "Content-Type":      "application/json",
            "x-api-key":         ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method = "POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            reply  = result["content"][0]["text"]
            return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
