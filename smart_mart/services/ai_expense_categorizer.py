"""AI Feature 10: Smart Expense Categorization

Auto-categorizes expenses using keyword matching.
ML-ready: can be upgraded to a trained classifier.
"""

from __future__ import annotations

import re

# Keyword → category mapping
EXPENSE_KEYWORDS = {
    "rent": {"category": "rent", "label": "Rent & Premises", "icon": "🏠"},
    "lease": {"category": "rent", "label": "Rent & Premises", "icon": "🏠"},
    "office": {"category": "rent", "label": "Rent & Premises", "icon": "🏠"},
    "salary": {"category": "salary", "label": "Staff Salaries", "icon": "👥"},
    "wage": {"category": "salary", "label": "Staff Salaries", "icon": "👥"},
    "staff": {"category": "salary", "label": "Staff Salaries", "icon": "👥"},
    "employee": {"category": "salary", "label": "Staff Salaries", "icon": "👥"},
    "bonus": {"category": "salary", "label": "Staff Salaries", "icon": "👥"},
    "electricity": {"category": "utilities", "label": "Utilities", "icon": "⚡"},
    "water": {"category": "utilities", "label": "Utilities", "icon": "💧"},
    "internet": {"category": "utilities", "label": "Utilities", "icon": "🌐"},
    "phone": {"category": "utilities", "label": "Utilities", "icon": "📱"},
    "bill": {"category": "utilities", "label": "Utilities", "icon": "📄"},
    "transport": {"category": "transport", "label": "Transport & Delivery", "icon": "🚚"},
    "delivery": {"category": "transport", "label": "Transport & Delivery", "icon": "🚚"},
    "fuel": {"category": "transport", "label": "Transport & Delivery", "icon": "⛽"},
    "vehicle": {"category": "transport", "label": "Transport & Delivery", "icon": "🚗"},
    "freight": {"category": "transport", "label": "Transport & Delivery", "icon": "📦"},
    "marketing": {"category": "marketing", "label": "Marketing & Advertising", "icon": "📢"},
    "advertising": {"category": "marketing", "label": "Marketing & Advertising", "icon": "📢"},
    "promotion": {"category": "marketing", "label": "Marketing & Advertising", "icon": "📢"},
    "social media": {"category": "marketing", "label": "Marketing & Advertising", "icon": "📢"},
    "repair": {"category": "maintenance", "label": "Maintenance & Repairs", "icon": "🔧"},
    "maintenance": {"category": "maintenance", "label": "Maintenance & Repairs", "icon": "🔧"},
    "cleaning": {"category": "maintenance", "label": "Maintenance & Repairs", "icon": "🧹"},
    "equipment": {"category": "maintenance", "label": "Maintenance & Repairs", "icon": "🔧"},
    "tax": {"category": "tax", "label": "Taxes & Fees", "icon": "🏛️"},
    "vat": {"category": "tax", "label": "Taxes & Fees", "icon": "🏛️"},
    "license": {"category": "tax", "label": "Taxes & Fees", "icon": "📋"},
    "permit": {"category": "tax", "label": "Taxes & Fees", "icon": "📋"},
    "insurance": {"category": "insurance", "label": "Insurance", "icon": "🛡️"},
    "purchase": {"category": "purchase", "label": "Stock Purchase", "icon": "🛒"},
    "stock": {"category": "purchase", "label": "Stock Purchase", "icon": "🛒"},
    "inventory": {"category": "purchase", "label": "Stock Purchase", "icon": "🛒"},
    "supplier": {"category": "purchase", "label": "Stock Purchase", "icon": "🛒"},
    "stationery": {"category": "miscellaneous", "label": "Office & Stationery", "icon": "✏️"},
    "printing": {"category": "miscellaneous", "label": "Office & Stationery", "icon": "🖨️"},
    "software": {"category": "miscellaneous", "label": "Software & Tech", "icon": "💻"},
    "subscription": {"category": "miscellaneous", "label": "Software & Tech", "icon": "💻"},
}


def categorize_expense(note: str, amount: float = 0) -> dict:
    """
    Auto-categorize an expense based on its description.

    Args:
        note: expense description/note
        amount: expense amount (used for additional context)

    Returns:
        {
            "category": str,
            "label": str,
            "icon": str,
            "confidence": float,
            "alternatives": [...]
        }
    """
    if not note:
        return {
            "category": "miscellaneous",
            "label": "Miscellaneous",
            "icon": "📌",
            "confidence": 0.0,
            "alternatives": [],
            "message": "No description provided.",
        }

    note_lower = note.lower()
    matches = []

    for keyword, info in EXPENSE_KEYWORDS.items():
        if keyword in note_lower:
            # Score based on keyword length (longer = more specific = higher confidence)
            score = len(keyword) / len(note_lower)
            score = min(0.95, score + 0.3)
            matches.append({
                "category": info["category"],
                "label": info["label"],
                "icon": info["icon"],
                "confidence": round(score, 2),
                "matched_keyword": keyword,
            })

    if not matches:
        # Amount-based heuristics
        if amount > 5000:
            return {
                "category": "rent",
                "label": "Rent & Premises",
                "icon": "🏠",
                "confidence": 0.3,
                "alternatives": [],
                "message": "Large amount — possibly rent or salary. Please verify.",
            }
        return {
            "category": "miscellaneous",
            "label": "Miscellaneous",
            "icon": "📌",
            "confidence": 0.2,
            "alternatives": [],
            "message": "Could not auto-categorize. Please select manually.",
        }

    matches.sort(key=lambda x: x["confidence"], reverse=True)
    best = matches[0]

    return {
        "category": best["category"],
        "label": best["label"],
        "icon": best["icon"],
        "confidence": best["confidence"],
        "alternatives": matches[1:3],
        "message": f"Auto-categorized as '{best['label']}' ({int(best['confidence']*100)}% confidence).",
    }


def batch_categorize(expenses: list[dict]) -> list[dict]:
    """Categorize a list of expenses."""
    results = []
    for exp in expenses:
        result = categorize_expense(exp.get("note", ""), float(exp.get("amount", 0)))
        result["expense_id"] = exp.get("id")
        result["original_note"] = exp.get("note")
        results.append(result)
    return results


def expense_category_summary() -> dict:
    """Summarize all expenses by AI-detected category."""
    from ..extensions import db
    from ..models.expense import Expense

    expenses = db.session.execute(db.select(Expense)).scalars().all()
    category_totals = {}

    for exp in expenses:
        result = categorize_expense(exp.note or exp.expense_type, float(exp.amount))
        cat = result["label"]
        if cat not in category_totals:
            category_totals[cat] = {"label": cat, "icon": result["icon"],
                                     "total": 0, "count": 0}
        category_totals[cat]["total"] += float(exp.amount)
        category_totals[cat]["count"] += 1

    items = sorted(category_totals.values(), key=lambda x: x["total"], reverse=True)
    grand_total = sum(i["total"] for i in items)

    for item in items:
        item["total"] = round(item["total"], 2)
        item["pct"] = round(item["total"] / grand_total * 100, 1) if grand_total else 0

    return {
        "categories": items,
        "grand_total": round(grand_total, 2),
        "top_category": items[0]["label"] if items else None,
    }
