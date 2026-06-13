"""
store_ai_service.py
===================
AI intelligence layer for the GoldKernel e-commerce storefront.

Features
--------
1. Store AI Chatbot          — Claude-powered customer Q&A with live product context
2. Smart Recommendations     — co-purchase affinity from OnlineOrderItem history
3. Selling Fast badges       — velocity scoring from recent order volume
4. AI Search Suggestions     — natural-language store search with live dropdown
5. Personalised Homepage     — category preference ranking for logged-in customers
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func

from ..extensions import db
from ..models.product import Product
from ..models.online_order import OnlineOrder, OnlineOrderItem


# ── Use shared cache_service (single source of truth for all caching) ─────────
# This replaces the old private _cache dict which was disconnected from the
# dashboard cache and caused double memory usage with no shared invalidation.

_CACHE_TTL = 300  # 5 minutes (seconds, matching cache_service convention)


def _cache_get(key: str):
    """Get from shared cache_service."""
    try:
        from .cache_service import get as _cs_get
        return _cs_get(f"store_ai:{key}")
    except Exception:
        return None


def _cache_set(key: str, value):
    """Set in shared cache_service."""
    try:
        from .cache_service import set as _cs_set
        _cs_set(f"store_ai:{key}", value, ttl=_CACHE_TTL)
    except Exception:
        pass
    return value


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 3 — Selling Fast / Popular badges
# ═══════════════════════════════════════════════════════════════════════════════

def get_velocity_map(days: int = 7, top_n: int = 20) -> dict[int, int]:
    """
    Return {product_id: units_sold_last_N_days} for the top N sellers.
    Cached 5 minutes — called on every home/product page.
    """
    key = f"velocity:{days}:{top_n}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        rows = db.session.execute(
            db.select(
                OnlineOrderItem.product_id,
                func.sum(OnlineOrderItem.quantity).label("sold"),
            )
            .join(OnlineOrder, OnlineOrder.id == OnlineOrderItem.order_id)
            .where(
                OnlineOrder.created_at >= cutoff,
                OnlineOrder.status.notin_(["cancelled", "returned"]),
            )
            .group_by(OnlineOrderItem.product_id)
            .order_by(func.sum(OnlineOrderItem.quantity).desc())
            .limit(top_n)
        ).all()
        result = {r.product_id: int(r.sold) for r in rows}
    except Exception:
        result = {}

    return _cache_set(key, result)


def selling_fast_ids(threshold_units: int = 3) -> set[int]:
    """Product IDs that sold >= threshold units in the last 7 days."""
    return {pid for pid, sold in get_velocity_map().items() if sold >= threshold_units}


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 2 — Smart Recommendations (co-purchase affinity)
# ═══════════════════════════════════════════════════════════════════════════════

def get_recommendations(product_id: int, limit: int = 4) -> list[Product]:
    """
    Return products frequently bought together with product_id.
    Uses co-occurrence in OnlineOrderItem rows (market basket style).
    Falls back to same-category products if no order history exists.
    """
    key = f"reco:{product_id}:{limit}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    try:
        # Find orders that contain this product
        order_ids = db.session.execute(
            db.select(OnlineOrderItem.order_id)
            .where(OnlineOrderItem.product_id == product_id)
            .limit(200)
        ).scalars().all()

        if order_ids:
            # Count co-occurrences of other products in those orders
            rows = db.session.execute(
                db.select(
                    OnlineOrderItem.product_id,
                    func.count(OnlineOrderItem.product_id).label("cnt"),
                )
                .where(
                    OnlineOrderItem.order_id.in_(order_ids),
                    OnlineOrderItem.product_id != product_id,
                )
                .group_by(OnlineOrderItem.product_id)
                .order_by(func.count(OnlineOrderItem.product_id).desc())
                .limit(limit)
            ).all()
            rec_ids = [r.product_id for r in rows]
            if rec_ids:
                products = db.session.execute(
                    db.select(Product).where(
                        Product.id.in_(rec_ids),
                        Product.is_active.isnot(False),
                        Product.quantity > 0,
                    )
                ).scalars().all()
                # Maintain affinity order
                id_to_prod = {p.id: p for p in products}
                result = [id_to_prod[pid] for pid in rec_ids if pid in id_to_prod]
                if result:
                    return _cache_set(key, result)
    except Exception:
        pass

    # Fallback: same-category products
    product = db.session.get(Product, product_id)
    if not product:
        return _cache_set(key, [])

    fallback = db.session.execute(
        db.select(Product)
        .where(
            Product.id != product_id,
            Product.is_active.isnot(False),
            Product.quantity > 0,
            func.lower(func.coalesce(Product.category, ""))
            == func.lower(func.coalesce(product.category, "")),
        )
        .order_by(func.random())
        .limit(limit)
    ).scalars().all()
    return _cache_set(key, fallback)


def get_cart_recommendations(cart_product_ids: list[int], limit: int = 4) -> list[Product]:
    """
    Recommend products for the cart page based on what's already in the cart.
    Union of recommendations for each item, deduplicated, excluding cart items.
    """
    if not cart_product_ids:
        return []

    seen: set[int] = set(cart_product_ids)
    recs: list[Product] = []
    for pid in cart_product_ids[:3]:  # limit DB calls
        for p in get_recommendations(pid, limit=3):
            if p.id not in seen:
                seen.add(p.id)
                recs.append(p)
        if len(recs) >= limit:
            break
    return recs[:limit]


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 5 — Personalised Homepage (category preference for logged-in customers)
# ═══════════════════════════════════════════════════════════════════════════════

def get_customer_preferred_categories(customer_phone: str, top_n: int = 3) -> list[str]:
    """
    Return the customer's most-purchased categories from order history.
    Used to re-rank the home product grid.
    """
    key = f"pref_cats:{customer_phone}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    try:
        rows = db.session.execute(
            db.select(
                Product.category,
                func.sum(OnlineOrderItem.quantity).label("qty"),
            )
            .join(Product, Product.id == OnlineOrderItem.product_id)
            .join(OnlineOrder, OnlineOrder.id == OnlineOrderItem.order_id)
            .where(
                OnlineOrder.customer_phone == customer_phone,
                OnlineOrder.status.notin_(["cancelled", "returned"]),
                Product.category.isnot(None),
            )
            .group_by(Product.category)
            .order_by(func.sum(OnlineOrderItem.quantity).desc())
            .limit(top_n)
        ).all()
        result = [r.category for r in rows if r.category]
    except Exception:
        result = []

    return _cache_set(key, result)


def personalise_products(products: list[Product], customer_phone: str) -> list[Product]:
    """
    Re-order products list so preferred categories appear first.
    Products not in preferred categories retain their original order.
    """
    if not customer_phone:
        return products
    preferred = get_customer_preferred_categories(customer_phone)
    if not preferred:
        return products

    pref_set = [c.lower() for c in preferred]

    def sort_key(p: Product) -> int:
        cat = (p.category or "").lower()
        try:
            return pref_set.index(cat)
        except ValueError:
            return len(pref_set)

    return sorted(products, key=sort_key)


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 4 — AI Search Suggestions (live dropdown)
# ═══════════════════════════════════════════════════════════════════════════════

def search_suggestions(q: str, limit: int = 6) -> list[dict]:
    """
    Fast product search for the live dropdown (called on every keystroke).
    Returns list of {id, name, category, price, image_filename, slug}.
    No Claude API call — pure DB, must be < 50ms.
    """
    if not q or len(q) < 2:
        return []

    q = q.strip()
    term = f"%{q.lower()}%"

    # Nepali aliases
    ALIASES = {
        "badam": "almond", "kaju": "cashew", "okhar": "walnut",
        "pista": "pistachio", "kismis": "raisin", "kishmish": "raisin",
        "khajur": "date", "khubani": "apricot", "anjeer": "fig",
        "akhrot": "walnut", "mungphali": "peanut",
    }
    expanded = ALIASES.get(q.lower(), q.lower())
    term2 = f"%{expanded}%"

    try:
        rows = db.session.execute(
            db.select(Product)
            .where(
                Product.is_active.isnot(False),
                Product.quantity > 0,
                db.or_(
                    func.lower(Product.name).like(term),
                    func.lower(Product.name).like(term2),
                    func.lower(func.coalesce(Product.category, "")).like(term),
                    func.lower(func.coalesce(Product.sku, "")).like(term),
                ),
            )
            .order_by(Product.name)
            .limit(limit)
        ).scalars().all()
    except Exception:
        return []

    return [
        {
            "id": p.id,
            "name": p.name,
            "category": p.category or "",
            "price": float(p.selling_price),
            "image_filename": p.image_filename or "",
            "slug": p.slug or "",
        }
        for p in rows
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 1 — Store AI Chatbot (Claude-powered)
# ═══════════════════════════════════════════════════════════════════════════════

def _build_product_context() -> str:
    """Build a compact product catalogue string for Claude's context window."""
    key = "chatbot_product_ctx"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    try:
        products = db.session.execute(
            db.select(Product)
            .where(Product.is_active.isnot(False), Product.quantity > 0)
            .order_by(Product.name)
            .limit(80)
        ).scalars().all()

        lines = []
        for p in products:
            desc = (p.description or "")[:80].replace("\n", " ")
            lines.append(
                f"- {p.name} | {p.category or 'Misc'} | NPR {float(p.selling_price):.0f}"
                f" | Qty:{p.quantity} | Pack:{p.pack_size or '-'}"
                + (f" | {desc}" if desc else "")
            )
        result = "\n".join(lines)
    except Exception:
        result = "Product catalogue unavailable."

    return _cache_set(key, result)


CHATBOT_SYSTEM = """You are the helpful shopping assistant for GoldKernel Dry Fruits & Treats, \
a premium dry fruits store in Nepal. You help customers find the right products, answer questions \
about ingredients, nutrition, gifting, and delivery.

RULES:
- Answer in 2-4 SHORT sentences. Be friendly and helpful.
- Currency is NPR (Nepali Rupees). Free delivery above NPR 2000, NPR 100 below.
- Delivery is available across Nepal. COD, eSewa, Khalti, IME Pay accepted.
- If asked about a specific product, reference the catalogue below.
- If a product is not in the catalogue, say it's currently unavailable.
- Do NOT make up prices or availability. Use only what's in the catalogue.
- Reply in the same language the customer writes in (Nepali or English).
- Keep replies concise — this is a chat widget, not an essay.

LIVE PRODUCT CATALOGUE:
{product_context}
"""


def chatbot_reply(
    message: str,
    history: list[dict] | None = None,
    customer_name: str | None = None,
) -> str:
    """
    Generate a Claude-powered reply for the store chatbot.
    Falls back to a keyword reply if ANTHROPIC_API_KEY is not set.

    Args:
        message:       Customer's latest message.
        history:       List of {role, content} prior turns (max last 6).
        customer_name: Logged-in customer name for personalisation.

    Returns:
        Reply string (plain text, no markdown).
    """
    message = (message or "").strip()
    if not message:
        return "How can I help you today?"

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _keyword_chatbot_reply(message)

    product_ctx = _build_product_context()
    system = CHATBOT_SYSTEM.format(product_context=product_ctx)
    if customer_name:
        system += f"\n\nThe customer's name is {customer_name}. Address them by name occasionally."

    # Build message list — last 6 turns of history
    messages = []
    if history:
        for turn in history[-6:]:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    try:
        payload = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 200,
            "system": system,
            "messages": messages,
        }).encode()

        import urllib.request as _urllib_req
        req = _urllib_req.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        with _urllib_req.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        return result["content"][0]["text"].strip()

    except Exception:
        return _keyword_chatbot_reply(message)


def _keyword_chatbot_reply(message: str) -> str:
    """Offline fallback chatbot using keyword matching."""
    msg = message.lower()

    if any(w in msg for w in ["delivery", "deliver", "shipping", "ship"]):
        return "We deliver across Nepal! Free delivery on orders above NPR 2000, otherwise NPR 100 flat delivery charge."
    if any(w in msg for w in ["payment", "pay", "esewa", "khalti", "cod", "cash"]):
        return "We accept Cash on Delivery (COD), eSewa, Khalti, and IME Pay."
    if any(w in msg for w in ["return", "refund", "exchange"]):
        return "We have a 7-day return policy. If you received a damaged or wrong product, please call us immediately."
    if any(w in msg for w in ["gift", "gifting", "present"]):
        return "Our premium gift boxes with mixed dry fruits (almonds, cashews, pistachios) are very popular for gifting! Prices start from NPR 500."
    if any(w in msg for w in ["price", "cost", "how much", "kati"]):
        return "Our prices vary by product. Browse our store to see current prices, or type the product name you're interested in!"
    if any(w in msg for w in ["track", "order", "status", "where"]):
        return "You can track your order at /store/track using your order number. Or check your account page if you're logged in."
    if any(w in msg for w in ["almond", "badam", "cashew", "kaju", "walnut", "okhar"]):
        return "Yes, we stock premium quality almonds, cashews, walnuts and more! Browse our full catalogue or add directly to cart."
    if any(w in msg for w in ["hello", "hi", "namaste", "hey"]):
        return "Namaste! Welcome to GoldKernel. How can I help you today? Ask me about our products, delivery, or anything else!"

    return "I'm here to help! Ask me about our dry fruits, delivery, payment options, or anything else about your order."
