"""AI Feature 7: Voice Assistant Backend

Processes voice commands transcribed by the browser's Web Speech API.
The frontend sends transcribed text → this backend processes it
→ returns spoken response text + action data.

Voice commands supported:
- "What are today's sales?"
- "Show low stock"
- "Add product [name]"
- "How much stock of [product]?"
- "Create new sale"
- "Show top products"
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func

from ..extensions import db
from ..models.product import Product
from ..models.sale import Sale, SaleItem
from . import ai_engine


def process_voice_command(transcript: str) -> dict:
    """
    Process a voice command and return response + action.

    Returns:
        {
            "spoken_response": str,  # text to speak back
            "action": str,           # frontend action to take
            "action_data": dict,     # data for the action
            "display_text": str,     # text to show in UI
        }
    """
    text = transcript.lower().strip()

    # ── Sales queries ─────────────────────────────────────────────────────
    if any(w in text for w in ["today sale", "today's sale", "aaj ko sale", "today revenue"]):
        today = date.today()
        total = db.session.execute(
            db.select(func.coalesce(func.sum(Sale.total_amount), 0))
            .where(func.date(Sale.sale_date) == today)
        ).scalar() or 0
        count = db.session.execute(
            db.select(func.count(Sale.id))
            .where(func.date(Sale.sale_date) == today)
        ).scalar() or 0
        spoken = f"Today's sales are {int(float(total))} rupees from {count} transactions."
        return {
            "spoken_response": spoken,
            "action": "navigate",
            "action_data": {"url": "/sales/"},
            "display_text": f"📊 Today: NPR {float(total):,.0f} ({count} sales)",
        }

    # ── Stock queries ─────────────────────────────────────────────────────
    if any(w in text for w in ["low stock", "stock low", "running out"]):
        products = db.session.execute(
            db.select(Product).where(Product.quantity <= 10).order_by(Product.quantity).limit(3)
        ).scalars().all()
        if products:
            names = ", ".join(p.name for p in products)
            spoken = f"Low stock alert. {len(products)} products need restocking: {names}."
        else:
            spoken = "All products have sufficient stock."
        return {
            "spoken_response": spoken,
            "action": "navigate",
            "action_data": {"url": "/alerts/"},
            "display_text": spoken,
        }

    # ── Stock of specific product ─────────────────────────────────────────
    if "stock of" in text or "how much" in text:
        all_products = db.session.execute(db.select(Product)).scalars().all()
        for p in all_products:
            if p.name.lower() in text:
                spoken = f"{p.name} has {p.quantity} {p.unit or 'units'} in stock."
                return {
                    "spoken_response": spoken,
                    "action": "highlight_product",
                    "action_data": {"product_id": p.id},
                    "display_text": f"📦 {p.name}: {p.quantity} {p.unit or 'pcs'}",
                }
        spoken = "I couldn't find that product. Please check the product name."
        return {"spoken_response": spoken, "action": "none", "action_data": {}, "display_text": spoken}

    # ── Navigation commands ───────────────────────────────────────────────
    if any(w in text for w in ["new sale", "create sale", "add sale", "make sale"]):
        return {
            "spoken_response": "Opening new sale form.",
            "action": "navigate",
            "action_data": {"url": "/sales/create"},
            "display_text": "🛒 Opening New Sale...",
        }

    if any(w in text for w in ["add product", "new product", "create product"]):
        return {
            "spoken_response": "Opening add product form.",
            "action": "navigate",
            "action_data": {"url": "/inventory/create"},
            "display_text": "📦 Opening Add Product...",
        }

    if any(w in text for w in ["dashboard", "home", "go home"]):
        return {
            "spoken_response": "Going to dashboard.",
            "action": "navigate",
            "action_data": {"url": "/dashboard/"},
            "display_text": "🏠 Going to Dashboard...",
        }

    if any(w in text for w in ["inventory", "products", "show products"]):
        return {
            "spoken_response": "Opening inventory.",
            "action": "navigate",
            "action_data": {"url": "/inventory/"},
            "display_text": "📦 Opening Inventory...",
        }

    if any(w in text for w in ["report", "reports", "analytics"]):
        return {
            "spoken_response": "Opening reports.",
            "action": "navigate",
            "action_data": {"url": "/reports/sales"},
            "display_text": "📊 Opening Reports...",
        }

    # ── Top products ──────────────────────────────────────────────────────
    if any(w in text for w in ["top product", "best seller", "best selling"]):
        month_start = date.today().replace(day=1)
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
            spoken = f"Your top selling product this month is {top.Product.name} with {top.qty} units sold."
        else:
            spoken = "No sales data available for this month."
        return {
            "spoken_response": spoken,
            "action": "none",
            "action_data": {},
            "display_text": f"🏆 {spoken}",
        }

    # ── Forecast ──────────────────────────────────────────────────────────
    if any(w in text for w in ["forecast", "predict", "tomorrow"]):
        forecasts = ai_engine.forecast_sales(days_ahead=1)
        if forecasts:
            f = forecasts[0]
            spoken = f"Tomorrow's predicted sales are approximately {int(f['predicted_sales'])} rupees."
        else:
            spoken = "Not enough data to forecast."
        return {
            "spoken_response": spoken,
            "action": "navigate",
            "action_data": {"url": "/ai/insights"},
            "display_text": f"🔮 {spoken}",
        }

    # ── Help ──────────────────────────────────────────────────────────────
    if any(w in text for w in ["help", "what can you", "commands"]):
        spoken = ("I can help with: today's sales, low stock, stock of a product, "
                  "new sale, add product, top products, forecast, dashboard, inventory, reports.")
        return {
            "spoken_response": spoken,
            "action": "none",
            "action_data": {},
            "display_text": spoken,
        }

    # ── Default ───────────────────────────────────────────────────────────
    spoken = "I didn't understand that command. Try saying: today's sales, low stock, new sale, or help."
    return {
        "spoken_response": spoken,
        "action": "none",
        "action_data": {},
        "display_text": spoken,
    }
