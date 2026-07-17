"""AI Feature 6: Image-based Product Recognition

Analyzes uploaded product images to:
- Extract product name suggestions from filename/metadata
- Suggest category based on visual keywords
- Suggest price range based on product type
- Auto-fill product form fields

Uses filename analysis + keyword matching (no external ML needed).
For production: integrate with Google Vision API or TensorFlow.
"""

from __future__ import annotations

import os
import re
from typing import Optional

from ..extensions import db
from ..models.product import Product
from ..models.category import Category


# Product keyword database — maps keywords to product info
PRODUCT_KNOWLEDGE_BASE = {
    # Grains
    "rice": {"category": "Grains & Pulses", "unit": "kg", "price_range": (80, 200)},
    "flour": {"category": "Grains & Pulses", "unit": "kg", "price_range": (60, 150)},
    "wheat": {"category": "Grains & Pulses", "unit": "kg", "price_range": (50, 120)},
    "dal": {"category": "Grains & Pulses", "unit": "kg", "price_range": (100, 300)},
    "lentil": {"category": "Grains & Pulses", "unit": "kg", "price_range": (100, 300)},
    # Oils
    "oil": {"category": "Oils & Fats", "unit": "L", "price_range": (150, 400)},
    "ghee": {"category": "Oils & Fats", "unit": "kg", "price_range": (500, 1500)},
    "butter": {"category": "Dairy & Eggs", "unit": "pcs", "price_range": (80, 300)},
    # Dairy
    "milk": {"category": "Dairy & Eggs", "unit": "L", "price_range": (80, 150)},
    "cheese": {"category": "Dairy & Eggs", "unit": "pcs", "price_range": (150, 500)},
    "yogurt": {"category": "Dairy & Eggs", "unit": "pcs", "price_range": (50, 200)},
    "egg": {"category": "Dairy & Eggs", "unit": "dozen", "price_range": (150, 250)},
    # Beverages
    "water": {"category": "Beverages", "unit": "pcs", "price_range": (20, 80)},
    "juice": {"category": "Beverages", "unit": "pcs", "price_range": (50, 200)},
    "cola": {"category": "Beverages", "unit": "pcs", "price_range": (50, 150)},
    "tea": {"category": "Beverages", "unit": "pcs", "price_range": (100, 500)},
    "coffee": {"category": "Beverages", "unit": "pcs", "price_range": (200, 800)},
    # Snacks
    "biscuit": {"category": "Snacks & Bakery", "unit": "pcs", "price_range": (20, 100)},
    "chips": {"category": "Snacks & Bakery", "unit": "pcs", "price_range": (20, 80)},
    "noodle": {"category": "Snacks & Bakery", "unit": "pcs", "price_range": (20, 60)},
    "bread": {"category": "Snacks & Bakery", "unit": "pcs", "price_range": (40, 120)},
    # Personal care
    "soap": {"category": "Personal Care & Hygiene", "unit": "pcs", "price_range": (50, 200)},
    "shampoo": {"category": "Personal Care & Hygiene", "unit": "pcs", "price_range": (100, 500)},
    "toothpaste": {"category": "Personal Care & Hygiene", "unit": "pcs", "price_range": (80, 250)},
    "detergent": {"category": "Household & Cleaning", "unit": "kg", "price_range": (100, 400)},
    # Medicine
    "tablet": {"category": "Medicine & Health", "unit": "pcs", "price_range": (10, 500)},
    "medicine": {"category": "Medicine & Health", "unit": "pcs", "price_range": (50, 1000)},
    "vitamin": {"category": "Medicine & Health", "unit": "pcs", "price_range": (200, 1000)},
    # Electronics
    "battery": {"category": "Electronics & Accessories", "unit": "pcs", "price_range": (50, 300)},
    "bulb": {"category": "Electronics & Accessories", "unit": "pcs", "price_range": (100, 500)},
    "charger": {"category": "Electronics & Accessories", "unit": "pcs", "price_range": (200, 1500)},
    # Spices
    "salt": {"category": "Spices & Condiments", "unit": "kg", "price_range": (30, 80)},
    "sugar": {"category": "Spices & Condiments", "unit": "kg", "price_range": (80, 150)},
    "spice": {"category": "Spices & Condiments", "unit": "pcs", "price_range": (50, 300)},
    "masala": {"category": "Spices & Condiments", "unit": "pcs", "price_range": (50, 300)},
}


def _ai_vision_analyze(image_path: str) -> dict | None:
    """Use Gemini vision to analyze a product image.
    Returns None if API key not set or call fails.
    """
    import os, base64
    from .gemini_client import gemini_vision, gemini_available
    if not gemini_available() or not os.path.exists(image_path):
        return None
    try:
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        ext = image_path.rsplit(".", 1)[-1].lower()
        media_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                     "gif": "image/gif", "webp": "image/webp"}
        media_type = media_map.get(ext, "image/jpeg")
        prompt = (
            "Look at this product image. Reply ONLY with a JSON object (no markdown) with these keys: "
            "name (string, product name), category (string, one of: "
            "Food & Grocery, Beverages, Personal Care, Household, Electronics, Clothing, Stationery, Other), "
            "suggested_price_min (number, NPR), suggested_price_max (number, NPR), "
            "description (string, 1 sentence). "
            "Base the price on Nepal market rates."
        )
        import json, re
        text = gemini_vision(image_bytes, media_type, prompt, max_tokens=300)
        if not text:
            return None
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = json.loads(text)
        return {
            "name": data.get("name", ""),
            "category": data.get("category", "Other"),
            "suggested_price_min": float(data.get("suggested_price_min", 0)),
            "suggested_price_max": float(data.get("suggested_price_max", 0)),
            "description": data.get("description", ""),
            "source": "gemini_vision",
        }
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug("Gemini vision failed: %s", exc)
        return None


def analyze_product_image(filename: str, file_size_bytes: int = 0,
                          image_path: str | None = None) -> dict:
    """
    Analyze a product image filename to suggest product details.

    Args:
        filename: original filename of the uploaded image
        file_size_bytes: file size for quality assessment

    Returns:
        Suggested product details for auto-filling the form.
    """
    # Clean filename
    name_raw = os.path.splitext(filename)[0]
    name_clean = re.sub(r'[_\-\.]+', ' ', name_raw).strip()
    name_lower = name_clean.lower()

    # Find matching keywords
    matched_keyword = None
    matched_info = None
    for keyword, info in PRODUCT_KNOWLEDGE_BASE.items():
        if keyword in name_lower:
            matched_keyword = keyword
            matched_info = info
            break

    # Generate product name suggestion
    suggested_name = _title_case(name_clean)

    # Check if product already exists
    existing = db.session.execute(
        db.select(Product).where(
            db.func.lower(Product.name).contains(name_lower[:10])
        ).limit(3)
    ).scalars().all()

    # Get available categories
    categories = db.session.execute(
        db.select(Category).order_by(Category.name)
    ).scalars().all()
    category_names = [c.name for c in categories]

    result = {
        "filename": filename,
        "suggested_name": suggested_name,
        "confidence": "high" if matched_keyword else "low",
        "similar_products": [{"id": p.id, "name": p.name, "sku": p.sku} for p in existing],
    }

    if matched_info:
        result.update({
            "suggested_category": matched_info["category"],
            "suggested_unit": matched_info["unit"],
            "suggested_price_min": matched_info["price_range"][0],
            "suggested_price_max": matched_info["price_range"][1],
            "suggested_selling_price": matched_info["price_range"][1],
            "suggested_cost_price": round(matched_info["price_range"][1] * 0.75, 2),
            "matched_keyword": matched_keyword,
            "insight": (
                f"Detected '{matched_keyword}' product. "
                f"Suggested category: {matched_info['category']}, "
                f"typical price: NPR {matched_info['price_range'][0]}–{matched_info['price_range'][1]}."
            ),
        })
    else:
        result.update({
            "suggested_category": category_names[0] if category_names else "Food & Grocery",
            "suggested_unit": "pcs",
            "suggested_selling_price": 100,
            "suggested_cost_price": 75,
            "insight": "Could not auto-detect product type. Please fill in details manually.",
        })

    return result


def _title_case(s: str) -> str:
    """Convert string to title case, handling common product name patterns."""
    words = s.split()
    result = []
    for w in words:
        if w.upper() in ("ML", "KG", "GM", "LTR", "PCS", "PKT"):
            result.append(w.upper())
        else:
            result.append(w.capitalize())
    return " ".join(result)


def recognize_from_filename(filename: str) -> dict:
    """Analyze a product image by filename — tries Claude vision if file exists."""
    import os
    from flask import current_app
    image_path = None
    try:
        upload_dir = os.path.join(current_app.static_folder, "uploads", "products")
        candidate = os.path.join(upload_dir, filename)
        if os.path.exists(candidate):
            image_path = candidate
    except Exception:
        pass

    if image_path:
        vision_result = _ai_vision_analyze(image_path)
        if vision_result:
            return vision_result

    return analyze_product_image(filename)


def recognize_from_text(text: str) -> dict:
    """Analyze a product by text/name description."""
    return analyze_product_image(text + ".jpg")
