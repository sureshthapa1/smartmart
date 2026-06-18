"""Nepal festival calendar for AI demand forecasting.

Contains major Nepal festivals with approximate Gregorian date ranges.
Used by the reorder bot and AI demand forecaster to anticipate stock surges.

Format: (name, month_range, dry_fruit_demand_multiplier, notes)
"""
from __future__ import annotations
from datetime import date, timedelta
from typing import List, Dict


# ── Festival data with typical Gregorian month windows ──────────────────────
# month_start, month_end are 1-indexed (January=1). Day ranges are approximate.
FESTIVALS: List[Dict] = [
    {
        "name": "Dashain (Vijaya Dashami)",
        "months": [9, 10],          # Sep–Oct
        "demand_mult": 3.5,
        "notes": "Biggest festival. Gift boxes, mixed dry fruits surge 3–4x. Order 3 weeks ahead.",
        "top_products": ["Almonds", "Cashews", "Pistachios", "Walnuts", "Mixed Dry Fruits"],
    },
    {
        "name": "Tihar (Deepawali)",
        "months": [10, 11],         # Oct–Nov
        "demand_mult": 3.0,
        "notes": "Second biggest. Sweets + dry fruits gifting. Kaju katri demand spikes. Order 2 weeks ahead.",
        "top_products": ["Cashews", "Almonds", "Raisins", "Dates", "Pistachios"],
    },
    {
        "name": "Chhath Puja",
        "months": [10, 11],
        "demand_mult": 2.0,
        "notes": "Makhana (fox nuts), dates, coconut high demand. 2 weeks before.",
        "top_products": ["Dates", "Coconut", "Almonds"],
    },
    {
        "name": "Holi (Fagu Purnima)",
        "months": [2, 3],           # Feb–Mar
        "demand_mult": 1.8,
        "notes": "Moderate surge. Snacking mixes and roasted nuts popular.",
        "top_products": ["Mixed Nuts", "Peanuts", "Cashews"],
    },
    {
        "name": "Teej",
        "months": [8, 9],           # Aug–Sep
        "demand_mult": 1.5,
        "notes": "Women's festival — fasting breaks with dry fruits. Dates and almonds popular.",
        "top_products": ["Dates", "Almonds", "Raisins"],
    },
    {
        "name": "Buddha Jayanti",
        "months": [4, 5],           # Apr–May
        "demand_mult": 1.4,
        "notes": "Moderate. Dry fruit offerings at monasteries. Raisins and figs.",
        "top_products": ["Raisins", "Figs", "Dates"],
    },
    {
        "name": "Janai Purnima (Raksha Bandhan)",
        "months": [7, 8],           # Jul–Aug
        "demand_mult": 1.6,
        "notes": "Gift exchange festival. Small dry fruit gift packs popular.",
        "top_products": ["Mixed Nuts", "Almonds", "Cashews"],
    },
    {
        "name": "Christmas / New Year",
        "months": [12, 1],          # Dec–Jan
        "demand_mult": 1.5,
        "notes": "Corporate gifting season. Premium gift boxes, imported nuts.",
        "top_products": ["Pistachios", "Macadamia", "Mixed Gift Packs"],
    },
    {
        "name": "Maghe Sankranti",
        "months": [1],              # Jan
        "demand_mult": 1.6,
        "notes": "Sesame (til) and chaku (molasses candy with nuts) traditional foods.",
        "top_products": ["Sesame Seeds", "Dates", "Peanuts"],
    },
    {
        "name": "Nepali New Year (Nava Varsha)",
        "months": [4],              # Apr
        "demand_mult": 1.7,
        "notes": "Gifting season. Premium boxes popular.",
        "top_products": ["Mixed Gift Packs", "Almonds", "Cashews", "Pistachios"],
    },
    {
        "name": "Indra Jatra",
        "months": [9],              # Sep
        "demand_mult": 1.4,
        "notes": "Kathmandu Valley festival. Moderate demand increase.",
        "top_products": ["Mixed Nuts", "Raisins"],
    },
    {
        "name": "Winter Season (Peak Dry Fruit)",
        "months": [11, 12, 1, 2],  # Nov–Feb
        "demand_mult": 1.8,
        "notes": "Cold weather drives dry fruit consumption year-round. Best sales period.",
        "top_products": ["Almonds", "Walnuts", "Dates", "Apricots", "All Products"],
    },
]


def get_upcoming_festivals(days_ahead: int = 45) -> List[Dict]:
    """Return festivals occurring in the next `days_ahead` days."""
    today = date.today()
    upcoming = []
    for fest in FESTIVALS:
        for month in fest["months"]:
            # Check if this month falls within the upcoming window
            year = today.year
            try:
                check_date = date(year, month, 15)  # mid-month as proxy
            except ValueError:
                continue
            if check_date < today:
                check_date = date(year + 1, month, 15)
            days_away = (check_date - today).days
            if 0 <= days_away <= days_ahead:
                upcoming.append({
                    **fest,
                    "days_away": days_away,
                    "approx_date": check_date.strftime("%B %Y"),
                })
                break  # Don't duplicate if festival spans 2 months
    return sorted(upcoming, key=lambda x: x["days_away"])


def get_festival_context_for_ai(days_ahead: int = 45) -> str:
    """Return a formatted string for AI context about upcoming festivals."""
    upcoming = get_upcoming_festivals(days_ahead)
    if not upcoming:
        return "No major Nepal festivals in the next 45 days."
    lines = ["UPCOMING NEPAL FESTIVALS (demand planning):"]
    for f in upcoming:
        lines.append(
            f"• {f['name']} — in ~{f['days_away']} days ({f['approx_date']}). "
            f"Demand multiplier: {f['demand_mult']}x. "
            f"Key products: {', '.join(f['top_products'][:3])}. "
            f"{f['notes']}"
        )
    return "\n".join(lines)
