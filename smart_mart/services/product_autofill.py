"""
product_autofill.py
===================
AI-powered product enrichment for SmartMart.

Priority
--------
1. Claude API (ANTHROPIC_API_KEY set) — generates rich, accurate content
   for ANY product name using Claude claude-haiku-4-5-20251001 (fast + cheap).
2. Keyword catalogue fallback — works offline, covers ~30 common product types.

Fields auto-filled
------------------
- description   : rich text with benefits, usage tips, storage info
- pack_size      : sensible default for the product type
- image_filename : downloaded from Pexels (best-match search query)
- slug           : SEO-friendly URL slug

Usage
-----
    from smart_mart.services.product_autofill import autofill_product
    updated = autofill_product(product, force=False)
    # updated = dict of field names that were changed
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Optional

# ── Image download ────────────────────────────────────────────────────────────

_UPLOADS_DIR: Optional[str] = None


def _uploads_dir() -> str:
    global _UPLOADS_DIR
    if _UPLOADS_DIR is None:
        here = os.path.dirname(os.path.dirname(__file__))  # smart_mart/
        _UPLOADS_DIR = os.path.join(here, "static", "uploads", "products")
        os.makedirs(_UPLOADS_DIR, exist_ok=True)
    return _UPLOADS_DIR


def _download_image(url: str, filename: str) -> bool:
    """Download image URL → uploads/products/filename. Returns True on success."""
    dest = os.path.join(_uploads_dir(), filename)
    if os.path.exists(dest) and os.path.getsize(dest) > 5_000:
        return True
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
        if len(data) < 5_000:
            return False
        with open(dest, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


def _pexels_image(search_query: str, filename: str) -> bool:
    """Search Pexels for an image matching query and download it."""
    # Try curated direct URLs first (no auth needed, free CDN)
    PEXELS_MAP = {
        "cashew":      "https://images.pexels.com/photos/4109080/pexels-photo-4109080.jpeg?w=600",
        "almond":      "https://images.pexels.com/photos/6157052/pexels-photo-6157052.jpeg?w=600",
        "walnut":      "https://images.pexels.com/photos/3630197/pexels-photo-3630197.jpeg?w=600",
        "pistachio":   "https://images.pexels.com/photos/5702716/pexels-photo-5702716.jpeg?w=600",
        "raisin":      "https://images.pexels.com/photos/6157050/pexels-photo-6157050.jpeg?w=600",
        "date":        "https://images.pexels.com/photos/6157049/pexels-photo-6157049.jpeg?w=600",
        "apricot":     "https://images.pexels.com/photos/3644742/pexels-photo-3644742.jpeg?w=600",
        "fig":         "https://images.pexels.com/photos/4051347/pexels-photo-4051347.jpeg?w=600",
        "coconut":     "https://images.pexels.com/photos/1528051/pexels-photo-1528051.jpeg?w=600",
        "peanut":      "https://images.pexels.com/photos/4110380/pexels-photo-4110380.jpeg?w=600",
        "rice":        "https://images.pexels.com/photos/4110255/pexels-photo-4110255.jpeg?w=600",
        "mustard oil": "https://images.pexels.com/photos/725997/pexels-photo-725997.jpeg?w=600",
        "oil":         "https://images.pexels.com/photos/725997/pexels-photo-725997.jpeg?w=600",
        "noodle":      "https://images.pexels.com/photos/1279330/pexels-photo-1279330.jpeg?w=600",
        "tea":         "https://images.pexels.com/photos/1638280/pexels-photo-1638280.jpeg?w=600",
        "coffee":      "https://images.pexels.com/photos/302899/pexels-photo-302899.jpeg?w=600",
        "milk":        "https://images.pexels.com/photos/248412/pexels-photo-248412.jpeg?w=600",
        "spice":       "https://images.pexels.com/photos/1340116/pexels-photo-1340116.jpeg?w=600",
        "soap":        "https://images.pexels.com/photos/2113855/pexels-photo-2113855.jpeg?w=600",
        "shampoo":     "https://images.pexels.com/photos/3735149/pexels-photo-3735149.jpeg?w=600",
        "toothpaste":  "https://images.pexels.com/photos/3762875/pexels-photo-3762875.jpeg?w=600",
        "chips":       "https://images.pexels.com/photos/1583884/pexels-photo-1583884.jpeg?w=600",
        "biscuit":     "https://images.pexels.com/photos/1028714/pexels-photo-1028714.jpeg?w=600",
        "juice":       "https://images.pexels.com/photos/338713/pexels-photo-338713.jpeg?w=600",
        "water":       "https://images.pexels.com/photos/1000084/pexels-photo-1000084.jpeg?w=600",
        "default":     "https://images.pexels.com/photos/5632388/pexels-photo-5632388.jpeg?w=600",
    }
    q = search_query.lower()
    for key, url in PEXELS_MAP.items():
        if key in q:
            return _download_image(url, filename)
    return _download_image(PEXELS_MAP["default"], filename)


# ── Claude AI autofill ────────────────────────────────────────────────────────

_AI_SYSTEM = """You are a product content writer for GoldKernel, a premium retail store in Nepal.
Given a product name and optional category, generate rich product information.

RULES:
- Write naturally for Nepali customers — use NPR currency when mentioning prices
- Be accurate — only mention real benefits/properties for the actual product
- Keep descriptions practical and useful for shopping decisions
- Use simple clear English (customers may not be fluent)
- For dry fruits/nuts: emphasise health benefits, how to use, storage
- For FMCG/groceries: emphasise usage, storage, quality indicators
- For personal care: emphasise benefits, how to use properly

RESPOND ONLY WITH VALID JSON — no markdown, no backticks, no preamble.
"""

_AI_PROMPT = """Product name: {name}
Category: {category}

Generate product content. Respond with this exact JSON structure:
{{
  "description": "2-3 paragraph rich description covering what it is, key benefits, and how to use it. Use \\n\\n between paragraphs. Include emoji bullet points for benefits (✅). Include a 💡 How to Use section. 150-250 words total.",
  "benefits": ["benefit 1", "benefit 2", "benefit 3", "benefit 4", "benefit 5"],
  "pack_size": "most common pack size for this product type e.g. 250g, 1kg, 500ml",
  "storage_tip": "one sentence on how to store this product",
  "origin": "where this product typically comes from e.g. Nepal, India, California",
  "image_search": "3-4 word Pexels image search query for a clean product photo e.g. cashew nuts bowl"
}}"""


def _claude_autofill(product_name: str, category: str = "") -> Optional[dict]:
    """
    Call Gemini API to generate product content.
    Returns parsed dict or None on failure.
    """
    from .gemini_client import gemini_generate, gemini_available
    if not gemini_available():
        return None

    prompt = _AI_PROMPT.format(
        name=product_name,
        category=category or "General",
    )

    try:
        raw = gemini_generate(prompt, system=_AI_SYSTEM, max_tokens=600, temperature=0.4)
        if not raw:
            return None
        # Strip markdown fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)
    except Exception:
        return None


# ── Keyword catalogue fallback ────────────────────────────────────────────────
# Compact version — just enough for offline fallback

CATALOGUE = [
    {"keywords": ["cashew", "kaju"],
     "description": "Premium cashew nuts — buttery, crunchy and rich in heart-healthy fats. Our W240-grade cashews are carefully selected for size and quality.\n\n✅ Benefits:\n• Rich in protein, magnesium and zinc\n• Supports heart health\n• Boosts energy and brain function\n\n💡 How to Use: Eat raw as a snack, add to kheer, curries or blend into cashew butter. Store in an airtight container.",
     "pack_size": "250g", "origin": "India/Vietnam", "image_search": "cashew nuts"},
    {"keywords": ["almond", "badam"],
     "description": "Premium California almonds — the world's most nutritious nut. Raw, unroasted and unsalted for maximum nutrition.\n\n✅ Benefits:\n• Lowers LDL cholesterol\n• High in Vitamin E for healthy skin\n• Regulates blood sugar\n• Rich in fibre for weight management\n\n💡 How to Use: Soak 6–8 almonds overnight and eat on an empty stomach for best results. Add to milk, smoothies or eat as a snack.",
     "pack_size": "250g", "origin": "California, USA", "image_search": "almond nuts"},
    {"keywords": ["walnut", "okhar", "akhrot"],
     "description": "Fresh Himalayan walnuts — the king of brain foods. Handpicked from high-altitude orchards with the highest plant-based omega-3 of any nut.\n\n✅ Benefits:\n• #1 plant source of omega-3 fatty acids\n• Boosts memory and cognitive function\n• Reduces bad cholesterol\n• Improves sleep quality\n\n💡 How to Use: Eat 4–5 halves daily. Great in oatmeal, salads, or paired with honey as an evening snack.",
     "pack_size": "250g", "origin": "Nepal/Kashmir", "image_search": "walnut nuts"},
    {"keywords": ["pistachio", "pista"],
     "description": "Premium Iranian pistachios — naturally split and full of flavour. One of the most antioxidant-rich nuts available.\n\n✅ Benefits:\n• Complete protein with all 9 essential amino acids\n• Reduces blood pressure\n• Protects eye health\n• Supports healthy weight\n\n💡 How to Use: Perfect as a snack, in desserts, rice dishes or ground into pistachio paste.",
     "pack_size": "200g", "origin": "Iran", "image_search": "pistachio nuts"},
    {"keywords": ["raisin", "kismis", "kishmish"],
     "description": "Premium sun-dried raisins — nature's candy packed with natural sweetness and iron.\n\n✅ Benefits:\n• Instant natural energy\n• Excellent source of iron — prevents anaemia\n• Improves digestion\n• Rich in antioxidants\n\n💡 How to Use: Add to kheer, halwa, pulao or eat as is. Soak in water overnight and drink as a morning tonic.",
     "pack_size": "250g", "origin": "India/Afghanistan", "image_search": "raisins dried"},
    {"keywords": ["date", "khajur", "medjool"],
     "description": "Premium Medjool dates — soft, naturally sweet with a rich caramel flavour. Cultivated for 6,000+ years for their remarkable nutrition.\n\n✅ Benefits:\n• Natural energy booster\n• High dietary fibre relieves constipation\n• Rich in iron and potassium\n• Anti-inflammatory properties\n\n💡 How to Use: Eat 2–3 daily as a natural sweetener. Use in smoothies, energy balls or stuff with nuts for a healthy dessert.",
     "pack_size": "250g", "origin": "Saudi Arabia/Iran", "image_search": "medjool dates"},
    {"keywords": ["apricot", "khubani"],
     "description": "Himalayan dried apricots — naturally sun-dried without preservatives from high-altitude valleys of Mustang and Ladakh.\n\n✅ Benefits:\n• Excellent for eye health (high in beta-carotene)\n• Boosts immunity with Vitamins A and C\n• Good source of iron\n• Natural digestive aid\n\n💡 How to Use: Eat as a snack, add to trail mix, yogurt, oatmeal or use in chutneys.",
     "pack_size": "200g", "origin": "Nepal/Ladakh", "image_search": "dried apricots"},
    {"keywords": ["fig", "anjeer"],
     "description": "Premium dried figs — honey-like sweetness with a satisfying chewy texture. One of the oldest cultivated fruits.\n\n✅ Benefits:\n• Very high in dietary fibre\n• Rich in calcium for bone health\n• Contains iron and magnesium\n• Natural remedy for constipation\n\n💡 How to Use: Soak overnight and eat in the morning. Add to smoothies, oatmeal or cheese boards.",
     "pack_size": "200g", "origin": "Turkey/Iran", "image_search": "dried figs"},
]

DEFAULT_FALLBACK = {
    "description": "Quality product carefully sourced for GoldKernel customers in Nepal.\n\n✅ Genuine product · Competitive price · Fast delivery across Nepal\n\n💡 Contact us for more details about this product.",
    "pack_size": None,
    "origin": "Nepal",
    "image_search": "product grocery",
}


def _keyword_fallback(product_name: str, category: str = "") -> dict:
    text = (product_name + " " + (category or "")).lower()
    best, best_score = None, 0
    for entry in CATALOGUE:
        score = sum(len(kw) for kw in entry["keywords"] if kw in text)
        if score > best_score:
            best_score, best = score, entry
    return best if best else DEFAULT_FALLBACK


# ── Slug helper ───────────────────────────────────────────────────────────────

def _make_slug(name: str, product_id: int) -> Optional[str]:
    try:
        from ..extensions import db
        from ..models.product import Product
        raw = re.sub(r"[^\w\s-]", "", name.lower()).strip()
        candidate = re.sub(r"[\s_-]+", "-", raw)[:120]
        if not candidate:
            return None
        conflict = db.session.execute(
            db.select(Product).where(
                Product.slug == candidate,
                Product.id != product_id,
            )
        ).scalar_one_or_none()
        if conflict:
            candidate = f"{candidate}-{product_id}"
        return candidate
    except Exception:
        return None


# ── Filename helper ───────────────────────────────────────────────────────────

def _safe_filename(product_id: int, name: str) -> str:
    safe = re.sub(r"[^\w]", "_", name.lower())[:40]
    return f"product_{product_id}_{safe}.jpg"


# ── Main public function ──────────────────────────────────────────────────────

def autofill_product(product, force: bool = False) -> dict:
    """
    Auto-fill product fields using Claude AI (or keyword fallback).
    Only fills empty fields unless force=True.

    Fields updated: description, pack_size, image_filename, slug

    Returns dict of field names → True for each field updated.
    """
    from ..extensions import db

    name = (product.name or "").strip()
    category = (product.category or "").strip()
    if not name:
        return {}

    updated = {}

    # ── 1. Get content data ──────────────────────────────────────────────────
    need_description  = force or not product.description
    need_pack_size    = force or not getattr(product, "pack_size", None)
    need_image        = force or not product.image_filename
    need_benefits     = force or not getattr(product, "benefits", None)
    need_origin       = force or not getattr(product, "origin", None)
    need_storage_tips = force or not getattr(product, "storage_tips", None)

    if need_description or need_pack_size or need_image or need_benefits or need_origin or need_storage_tips:
        # Try Claude first
        data = _claude_autofill(name, category)
        if not data:
            # Keyword fallback
            data = _keyword_fallback(name, category)

        # ── 2. Description ───────────────────────────────────────────────────
        if need_description and data.get("description"):
            product.description = data["description"]
            updated["description"] = True

        # ── 2b. Benefits (own column — list joined as markdown bullets) ──────
        if need_benefits and data.get("benefits"):
            benefits_raw = data["benefits"]
            if isinstance(benefits_raw, list):
                product.benefits = "\n".join(f"- {b}" for b in benefits_raw if b)
            elif isinstance(benefits_raw, str):
                product.benefits = benefits_raw
            if product.benefits:
                updated["benefits"] = True

        # ── 2c. Origin (own column) ───────────────────────────────────────────
        if need_origin and data.get("origin"):
            product.origin = str(data["origin"])[:120]  # respect column length
            updated["origin"] = True

        # ── 2d. Storage tips (own column) ─────────────────────────────────────
        if need_storage_tips and data.get("storage_tip"):
            product.storage_tips = data["storage_tip"]
            updated["storage_tips"] = True

        # ── 3. Pack size ─────────────────────────────────────────────────────
        if need_pack_size and data.get("pack_size"):
            product.pack_size = data["pack_size"]
            updated["pack_size"] = True

        # ── 4. Image ─────────────────────────────────────────────────────────
        if need_image:
            filename = _safe_filename(product.id, name)
            image_query = data.get("image_search") or name
            downloaded = _pexels_image(image_query, filename)
            if downloaded:
                # Try Cloudinary upload for persistent storage (Render ephemeral FS fix)
                final_identifier = filename
                try:
                    from .image_service import _cloudinary_available, _cloudinary
                    if _cloudinary_available():
                        cl = _cloudinary()
                        if cl:
                            local_path = os.path.join(_uploads_dir(), filename)
                            if os.path.exists(local_path):
                                result = cl.uploader.upload(
                                    local_path,
                                    folder="smartmart/products",
                                    transformation=[
                                        {"width": 800, "height": 800, "crop": "limit",
                                         "quality": "auto:good"},
                                    ],
                                    resource_type="image",
                                )
                                final_identifier = "cld:" + result["public_id"]
                                try:
                                    os.remove(local_path)  # clean up local after cloud upload
                                except Exception:
                                    pass
                except Exception:
                    pass  # Keep local file if Cloudinary upload fails
                product.image_filename = final_identifier
                updated["image_filename"] = True

    # ── 5. Slug (always fill if missing) ────────────────────────────────────
    if force or not getattr(product, "slug", None):
        slug = _make_slug(name, product.id)
        if slug:
            product.slug = slug
            updated["slug"] = True

    # ── 6. Commit ────────────────────────────────────────────────────────────
    if updated:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    return updated


# ── Async-friendly bulk autofill ─────────────────────────────────────────────

def autofill_all_empty(limit: int = 50) -> dict:
    """
    Autofill the first `limit` products that are missing description or image.
    Called from a background task or admin route.
    Returns summary dict.
    """
    from ..extensions import db
    from ..models.product import Product

    products = db.session.execute(
        db.select(Product)
        .where(
            db.or_(
                Product.description.is_(None),
                Product.description == "",
                Product.image_filename.is_(None),
            ),
            Product.is_active.isnot(False),
        )
        .limit(limit)
    ).scalars().all()

    results = {"total": len(products), "updated": 0, "skipped": 0}
    for product in products:
        try:
            changed = autofill_product(product, force=False)
            if changed:
                results["updated"] += 1
            else:
                results["skipped"] += 1
        except Exception:
            results["skipped"] += 1

    return results
