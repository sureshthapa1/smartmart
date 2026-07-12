"""
recommendation_service.py — Advanced Product Recommendations
=============================================================
Combines three signals to recommend products:

1. Co-purchase affinity       — products bought together in online orders
2. Collaborative filtering    — "customers who bought X also bought Y"
                                using purchase-history similarity
3. LLM-powered suggestions    — Claude-generated "complete the basket" recs
                                (used when order history is sparse)

All results are cached via the shared cache_service.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from typing import Optional

from sqlalchemy import func

from ..extensions import db
from ..models.product import Product
from ..models.online_order import OnlineOrder, OnlineOrderItem
from ..models.sale import Sale, SaleItem

logger = logging.getLogger(__name__)

_CACHE_TTL = 600  # 10 minutes


def _cget(key: str):
    try:
        from .cache_service import get as _g
        return _g(f"reco:{key}")
    except Exception:
        return None


def _cset(key: str, value):
    try:
        from .cache_service import set as _s
        _s(f"reco:{key}", value, ttl=_CACHE_TTL)
    except Exception:
        pass
    return value


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Co-purchase affinity (market basket)
# ═══════════════════════════════════════════════════════════════════════════════

def _copurchase_recs(product_id: int, limit: int = 6) -> list[int]:
    """Return product IDs most frequently bought together with product_id."""
    key = f"copurchase:{product_id}:{limit}"
    cached = _cget(key)
    if cached is not None:
        return cached

    try:
        # Combine online orders + POS sale items for broader signal
        # Online orders
        order_ids = db.session.execute(
            db.select(OnlineOrderItem.order_id)
            .where(OnlineOrderItem.product_id == product_id)
            .limit(500)
        ).scalars().all()

        co_counts: dict[int, int] = defaultdict(int)

        if order_ids:
            rows = db.session.execute(
                db.select(OnlineOrderItem.product_id, func.count().label("cnt"))
                .where(
                    OnlineOrderItem.order_id.in_(order_ids),
                    OnlineOrderItem.product_id != product_id,
                )
                .group_by(OnlineOrderItem.product_id)
                .order_by(func.count().desc())
                .limit(20)
            ).all()
            for r in rows:
                co_counts[r.product_id] += r.cnt

        # POS sale items
        sale_ids = db.session.execute(
            db.select(SaleItem.sale_id)
            .where(SaleItem.product_id == product_id)
            .limit(500)
        ).scalars().all()

        if sale_ids:
            rows = db.session.execute(
                db.select(SaleItem.product_id, func.count().label("cnt"))
                .where(
                    SaleItem.sale_id.in_(sale_ids),
                    SaleItem.product_id != product_id,
                )
                .group_by(SaleItem.product_id)
                .order_by(func.count().desc())
                .limit(20)
            ).all()
            for r in rows:
                co_counts[r.product_id] += r.cnt

        result = sorted(co_counts, key=lambda k: co_counts[k], reverse=True)[:limit]
        return _cset(key, result)

    except Exception as exc:
        logger.debug("copurchase_recs failed: %s", exc)
        return _cset(key, [])


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Collaborative filtering  (user-based similarity)
# ═══════════════════════════════════════════════════════════════════════════════

def _collab_recs(customer_phone: str, limit: int = 8) -> list[int]:
    """
    Recommend products based on customers with similar purchase patterns.
    Returns product IDs not yet purchased by this customer.
    """
    if not customer_phone:
        return []

    key = f"collab:{customer_phone}:{limit}"
    cached = _cget(key)
    if cached is not None:
        return cached

    try:
        # This customer's purchased product IDs
        my_products = set(db.session.execute(
            db.select(OnlineOrderItem.product_id.distinct())
            .join(OnlineOrder, OnlineOrder.id == OnlineOrderItem.order_id)
            .where(OnlineOrder.customer_phone == customer_phone)
        ).scalars().all())

        if not my_products:
            return _cset(key, [])

        # Find other customers who bought at least 2 of the same products
        similar_customers = db.session.execute(
            db.select(OnlineOrder.customer_phone, func.count().label("overlap"))
            .join(OnlineOrderItem, OnlineOrderItem.order_id == OnlineOrder.id)
            .where(
                OnlineOrderItem.product_id.in_(my_products),
                OnlineOrder.customer_phone != customer_phone,
                OnlineOrder.customer_phone.isnot(None),
            )
            .group_by(OnlineOrder.customer_phone)
            .having(func.count() >= 2)
            .order_by(func.count().desc())
            .limit(20)
        ).all()

        if not similar_customers:
            return _cset(key, [])

        similar_phones = [r.customer_phone for r in similar_customers]

        # Products those customers bought that this customer hasn't
        candidate_rows = db.session.execute(
            db.select(OnlineOrderItem.product_id, func.count().label("pop"))
            .join(OnlineOrder, OnlineOrder.id == OnlineOrderItem.order_id)
            .where(
                OnlineOrder.customer_phone.in_(similar_phones),
                OnlineOrderItem.product_id.notin_(my_products),
            )
            .group_by(OnlineOrderItem.product_id)
            .order_by(func.count().desc())
            .limit(limit * 2)
        ).all()

        result = [r.product_id for r in candidate_rows][:limit]
        return _cset(key, result)

    except Exception as exc:
        logger.debug("collab_recs failed: %s", exc)
        return _cset(key, [])


# ═══════════════════════════════════════════════════════════════════════════════
# 3. LLM-powered recommendations (Claude)
# ═══════════════════════════════════════════════════════════════════════════════

def _llm_recs(product_name: str, category: str, available_names: list[str]) -> list[str]:
    """
    Ask Gemini which products pair well with `product_name`.
    Returns list of product names from available_names.
    """
    from .gemini_client import gemini_generate, gemini_available
    if not gemini_available() or not available_names:
        return []

    try:
        catalogue_preview = "\n".join(f"- {n}" for n in available_names[:60])
        prompt = (
            f"A customer is looking at: {product_name} ({category}).\n"
            f"From the product list below, pick up to 4 that pair well with it "
            f"(complementary flavours, gift combinations, or frequently used together).\n"
            f"Reply with ONLY a JSON array of product names, e.g. [\"Almonds 500g\", \"Cashews 250g\"]\n\n"
            f"Products:\n{catalogue_preview}"
        )
        text = gemini_generate(prompt, max_tokens=150, temperature=0.3)
        if not text:
            return []
        import re
        m = re.search(r'\[.*?\]', text, re.DOTALL)
        if m:
            names = json.loads(m.group())
            return [n for n in names if isinstance(n, str) and n in available_names]
    except Exception as exc:
        logger.debug("llm_recs failed: %s", exc)
    return []


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════

def get_product_recommendations(
    product_id: int,
    customer_phone: Optional[str] = None,
    limit: int = 6,
    use_llm_fallback: bool = True,
) -> list[Product]:
    """
    Get the best recommendations for a product page.
    Merges co-purchase + collab signals, falls back to LLM if history sparse.
    """
    key = f"prod:{product_id}:{customer_phone}:{limit}"
    cached = _cget(key)
    if cached is not None:
        return cached

    seen: set[int] = {product_id}
    candidate_ids: list[int] = []

    # Signal 1: co-purchase
    for pid in _copurchase_recs(product_id, limit=limit * 2):
        if pid not in seen:
            seen.add(pid)
            candidate_ids.append(pid)

    # Signal 2: collab (if logged in)
    if customer_phone:
        for pid in _collab_recs(customer_phone, limit=limit):
            if pid not in seen:
                seen.add(pid)
                candidate_ids.append(pid)

    # Fetch products for candidates
    result_products: list[Product] = []
    if candidate_ids:
        id_to_product = {
            p.id: p for p in db.session.execute(
                db.select(Product).where(
                    Product.id.in_(candidate_ids[:limit * 2]),
                    Product.is_active.isnot(False),
                    Product.quantity > 0,
                )
            ).scalars().all()
        }
        # Preserve ranking order
        result_products = [id_to_product[pid] for pid in candidate_ids if pid in id_to_product]

    # Signal 3: LLM fallback when history sparse
    if len(result_products) < 3 and use_llm_fallback:
        product = db.session.get(Product, product_id)
        if product:
            all_names = db.session.execute(
                db.select(Product.name)
                .where(Product.is_active.isnot(False), Product.quantity > 0, Product.id != product_id)
                .order_by(Product.name)
                .limit(100)
            ).scalars().all()
            llm_names = _llm_recs(product.name, product.category or "", list(all_names))
            if llm_names:
                llm_products = db.session.execute(
                    db.select(Product).where(
                        Product.name.in_(llm_names),
                        Product.is_active.isnot(False),
                        Product.quantity > 0,
                    )
                ).scalars().all()
                for p in llm_products:
                    if p.id not in {rp.id for rp in result_products}:
                        result_products.append(p)

    # Fallback: same-category
    if not result_products:
        product = db.session.get(Product, product_id)
        if product:
            result_products = db.session.execute(
                db.select(Product).where(
                    Product.id != product_id,
                    Product.is_active.isnot(False),
                    Product.quantity > 0,
                    func.lower(func.coalesce(Product.category, ""))
                    == func.lower(func.coalesce(product.category, "")),
                ).order_by(func.random()).limit(limit)
            ).scalars().all()

    final = result_products[:limit]
    _cset(key, final)
    return final


def get_cart_recommendations(cart_product_ids: list[int], limit: int = 4) -> list[Product]:
    """Recommendations for the cart page — union of per-item co-purchase signals."""
    if not cart_product_ids:
        return []

    key = f"cart:{'_'.join(str(i) for i in sorted(cart_product_ids))}:{limit}"
    cached = _cget(key)
    if cached is not None:
        return cached

    seen: set[int] = set(cart_product_ids)
    candidate_ids: list[int] = []

    for pid in cart_product_ids[:4]:
        for cid in _copurchase_recs(pid, limit=4):
            if cid not in seen:
                seen.add(cid)
                candidate_ids.append(cid)

    if not candidate_ids:
        return _cset(key, [])

    products = db.session.execute(
        db.select(Product).where(
            Product.id.in_(candidate_ids[:limit * 2]),
            Product.is_active.isnot(False),
            Product.quantity > 0,
        )
    ).scalars().all()

    id_map = {p.id: p for p in products}
    result = [id_map[pid] for pid in candidate_ids if pid in id_map][:limit]
    return _cset(key, result)


def invalidate_customer_cache(customer_phone: str) -> None:
    """Call after a customer places a new order."""
    try:
        from .cache_service import delete as _del
        _del(f"reco:collab:{customer_phone}")
    except Exception:
        pass
