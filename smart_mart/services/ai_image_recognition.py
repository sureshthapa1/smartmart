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


def analyze_product_image(filename: str, file_size_bytes: int = 0) -> dict:
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


def batch_analyze_images(filenames: list[str]) -> list[dict]:
    """Analyze multiple product images at once."""
    return [analyze_product_image(f) for f in filenames]
