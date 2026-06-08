"""
product_autofill.py
Auto-generates description, pack_size, and product image for any product
based on its name. Called automatically after create/edit in inventory.

Matching is keyword-based — no external API needed, works offline.
Images are downloaded from Pexels (free, no auth) on first use and cached.
"""
from __future__ import annotations

import os
import re
import urllib.request
from typing import Optional

# ── Image download helper ─────────────────────────────────────────────────────

_UPLOADS_DIR: Optional[str] = None


def _uploads_dir() -> str:
    global _UPLOADS_DIR
    if _UPLOADS_DIR is None:
        here = os.path.dirname(os.path.dirname(__file__))  # smart_mart/
        _UPLOADS_DIR = os.path.join(here, "static", "uploads", "products")
        os.makedirs(_UPLOADS_DIR, exist_ok=True)
    return _UPLOADS_DIR


def _download_image(url: str, filename: str) -> bool:
    """Download image URL and save as filename in uploads. Returns True on success."""
    dest = os.path.join(_uploads_dir(), filename)
    if os.path.exists(dest) and os.path.getsize(dest) > 10_000:
        return True  # already cached
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
        if len(data) < 10_000:
            return False
        with open(dest, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


# ── Catalogue — keyword → product data ───────────────────────────────────────
# Each entry:
#   keywords: list of lowercase strings to match against product name
#   description: full rich text shown on store
#   pack_size: default pack size
#   image_urls: list of Pexels direct CDN URLs to try in order

CATALOGUE = [
    # ── DRY FRUITS ────────────────────────────────────────────────────────────
    {
        "keywords": ["cashew", "kaju"],
        "description": (
            "Premium cashew nuts — buttery, crunchy and irresistible. "
            "Our cashews are whole W240-grade kernels, carefully selected for size and quality. "
            "Rich in heart-healthy monounsaturated fats, plant-based protein, magnesium, "
            "zinc, and copper.\n\n"
            "✅ Key Benefits:\n"
            "• Supports heart health and lowers bad cholesterol\n"
            "• Rich source of plant-based protein (5g per 30g serving)\n"
            "• High in magnesium — boosts energy and reduces fatigue\n"
            "• Strengthens bones and teeth\n"
            "• Great for brain function and immune support\n\n"
            "💡 How to Use: Eat raw as a snack, add to kheer, biryani, curries, "
            "ladoos, or blend into creamy cashew butter and cashew milk. "
            "Store in an airtight container away from sunlight."
        ),
        "pack_size": "250g",
        "image_urls": [
            "https://images.pexels.com/photos/4109080/pexels-photo-4109080.jpeg?auto=compress&cs=tinysrgb&w=600",
            "https://images.pexels.com/photos/4110380/pexels-photo-4110380.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["almond", "badam"],
        "description": (
            "Premium California almonds — the world's most nutritious nut. "
            "Our almonds are raw, unroasted, and unsalted — preserving every nutrient. "
            "One of the most researched foods for heart health, brain function, and weight management.\n\n"
            "✅ Key Benefits:\n"
            "• Lowers LDL (bad) cholesterol and reduces heart disease risk\n"
            "• Regulates blood sugar — ideal for diabetics\n"
            "• High in Vitamin E — promotes healthy glowing skin\n"
            "• Rich in fibre — keeps you full and aids weight loss\n"
            "• Strengthens bones with calcium and phosphorus\n\n"
            "💡 How to Use: Soak 6–8 almonds overnight and eat on an empty stomach "
            "for maximum benefit. Add to milk, smoothies, kheer, halwa, or eat as a snack. "
            "Also great as almond flour in baking."
        ),
        "pack_size": "250g",
        "image_urls": [
            "https://images.pexels.com/photos/6157052/pexels-photo-6157052.jpeg?auto=compress&cs=tinysrgb&w=600",
            "https://images.pexels.com/photos/57042/pexels-photo-57042.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["walnut", "okhar", "akhrot"],
        "description": (
            "Fresh Himalayan walnuts — the king of brain foods. "
            "Handpicked from high-altitude orchards of Nepal and Kashmir. Light, crisp, "
            "and packed with the highest plant-based omega-3 content of any nut. "
            "Their unique brain-like shape mirrors their greatest benefit.\n\n"
            "✅ Key Benefits:\n"
            "• #1 plant source of omega-3 fatty acids (ALA)\n"
            "• Significantly boosts memory and cognitive function\n"
            "• Powerful antioxidants fight inflammation and aging\n"
            "• Reduces bad cholesterol and blood pressure\n"
            "• Improves sleep quality through melatonin production\n\n"
            "💡 How to Use: Eat 4–5 halves daily for best results. "
            "Excellent in oatmeal, salads, baked goods, energy bars, "
            "or paired with honey as an evening snack."
        ),
        "pack_size": "250g",
        "image_urls": [
            "https://images.pexels.com/photos/3630197/pexels-photo-3630197.jpeg?auto=compress&cs=tinysrgb&w=600",
            "https://images.pexels.com/photos/867470/pexels-photo-867470.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["pistachio", "pista"],
        "description": (
            "Premium Iranian pistachios — the smiling green nut. "
            "Our pistachios are large, naturally split, and lightly salted. "
            "Cultivated for over 9,000 years, pistachios are one of the most "
            "antioxidant-rich nuts in the world.\n\n"
            "✅ Key Benefits:\n"
            "• Complete protein — contains all 9 essential amino acids\n"
            "• Reduces blood pressure and inflammation\n"
            "• Lowers blood sugar levels\n"
            "• Rich in lutein and zeaxanthin — protects eye health\n"
            "• High fibre content supports gut health and healthy weight\n\n"
            "💡 How to Use: Perfect as a snack, in baklava, kulfi, ice cream, "
            "rice dishes, or ground into pistachio paste for desserts and sauces."
        ),
        "pack_size": "200g",
        "image_urls": [
            "https://images.pexels.com/photos/5702716/pexels-photo-5702716.jpeg?auto=compress&cs=tinysrgb&w=600",
            "https://images.pexels.com/photos/5945755/pexels-photo-5945755.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["raisin", "kismis", "kishmish", "munakka"],
        "description": (
            "Golden raisins and dark raisins — nature's candy. "
            "Sun-dried from premium grape varieties, retaining natural sweetness "
            "and concentrated nutrients. A beloved ingredient in Nepali sweets, "
            "biryanis, and traditional medicine for centuries.\n\n"
            "✅ Key Benefits:\n"
            "• Instant natural energy — perfect pre-workout snack\n"
            "• Excellent source of iron — prevents and treats anaemia\n"
            "• High in antioxidants — resveratrol fights cellular aging\n"
            "• Improves digestion and relieves constipation\n"
            "• Boosts bone density with calcium and boron\n\n"
            "💡 How to Use: Add to kheer, halwa, pulao, bread, muesli, "
            "or eat as is. Soak 20–30 raisins in water overnight — "
            "drink the water in the morning as a natural energy tonic."
        ),
        "pack_size": "250g",
        "image_urls": [
            "https://images.pexels.com/photos/6157050/pexels-photo-6157050.jpeg?auto=compress&cs=tinysrgb&w=600",
            "https://images.pexels.com/photos/1120575/pexels-photo-1120575.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["date", "khajur", "medjool", "ajwa"],
        "description": (
            "Medjool and Ajwa dates — the fruit of paradise. "
            "Imported from Saudi Arabia and Iran, our premium dates are soft, "
            "moist, and naturally sweet with a rich caramel flavour. "
            "Dates have been cultivated for over 6,000 years and are revered "
            "across the Middle East and South Asia for their remarkable nutrition.\n\n"
            "✅ Key Benefits:\n"
            "• Natural energy booster — better than processed sugar\n"
            "• High in dietary fibre — relieves constipation naturally\n"
            "• Rich in iron and potassium — supports heart and blood health\n"
            "• Contains B vitamins — boosts brain health and memory\n"
            "• Anti-inflammatory compounds support joint health\n\n"
            "💡 How to Use: Eat 2–3 dates daily as a natural sweetener replacement. "
            "Use in smoothies, energy balls, cakes, or stuff with peanut butter "
            "and nuts for a healthy dessert."
        ),
        "pack_size": "250g",
        "image_urls": [
            "https://images.pexels.com/photos/6157049/pexels-photo-6157049.jpeg?auto=compress&cs=tinysrgb&w=600",
            "https://images.pexels.com/photos/3650561/pexels-photo-3650561.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["apricot", "khubani"],
        "description": (
            "Himalayan dried apricots — sweet, tangy, and packed with goodness. "
            "Sourced from the high-altitude valleys of Mustang (Nepal) and Ladakh (India), "
            "our dried apricots are naturally sun-dried without preservatives or added sugar. "
            "Their deep orange colour signals exceptional beta-carotene content.\n\n"
            "✅ Key Benefits:\n"
            "• Excellent for eye health — high in beta-carotene (Vitamin A)\n"
            "• High in dietary fibre — improves digestion\n"
            "• Rich in Vitamins A and C — boosts immunity and skin health\n"
            "• Good source of iron — helps prevent anaemia\n"
            "• Natural laxative — gentle relief from constipation\n\n"
            "💡 How to Use: Eat as a healthy snack, add to trail mix, "
            "yogurt, oatmeal, or use in chutneys and lamb dishes. "
            "Also excellent in traditional Nepali achar."
        ),
        "pack_size": "200g",
        "image_urls": [
            "https://images.pexels.com/photos/3644742/pexels-photo-3644742.jpeg?auto=compress&cs=tinysrgb&w=600",
            "https://images.pexels.com/photos/5946640/pexels-photo-5946640.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["coconut", "nariyal", "nariwal", "dried coconut"],
        "description": (
            "Dried coconut — the versatile tropical staple. "
            "Freshly shredded and naturally dehydrated, retaining maximum flavour "
            "and nutrition. Rich in medium-chain triglycerides (MCTs) — a unique "
            "fat that is rapidly converted to energy by the body.\n\n"
            "✅ Key Benefits:\n"
            "• MCTs provide quick, sustained energy without fat storage\n"
            "• Boosts metabolism and aids healthy weight management\n"
            "• Rich in manganese — supports bone health and metabolism\n"
            "• Antifungal and antibacterial properties\n"
            "• Supports gut health with high fibre content\n\n"
            "💡 How to Use: Add to curries, chutneys, ladoos, barfi, "
            "coconut rice, and desserts. Sprinkle toasted coconut over "
            "oatmeal or blend into smoothies and energy bars."
        ),
        "pack_size": "200g",
        "image_urls": [
            "https://images.pexels.com/photos/1528051/pexels-photo-1528051.jpeg?auto=compress&cs=tinysrgb&w=600",
            "https://images.pexels.com/photos/1398448/pexels-photo-1398448.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["fig", "anjeer"],
        "description": (
            "Premium dried figs — nature's natural sweetener. "
            "Our figs are sun-dried to perfection, with a honey-like sweetness "
            "and a satisfying chewy texture. One of the oldest cultivated fruits.\n\n"
            "✅ Key Benefits:\n"
            "• Very high in dietary fibre — excellent for digestion\n"
            "• Rich in calcium — supports bone health\n"
            "• Contains iron, magnesium, and potassium\n"
            "• Antioxidants protect against cell damage\n"
            "• Natural remedy for constipation\n\n"
            "💡 How to Use: Soak overnight and eat in the morning. "
            "Add to smoothies, oatmeal, cheese boards, or use in baking."
        ),
        "pack_size": "200g",
        "image_urls": [
            "https://images.pexels.com/photos/4051347/pexels-photo-4051347.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["peanut", "mungphali", "groundnut"],
        "description": (
            "Fresh roasted peanuts — the affordable powerhouse nut. "
            "Our peanuts are lightly roasted and lightly salted, retaining "
            "maximum nutrition. Despite being affordable, peanuts match or "
            "beat most expensive nuts in protein content.\n\n"
            "✅ Key Benefits:\n"
            "• Highest protein content of any nut (26g per 100g)\n"
            "• Rich in niacin (B3) — supports brain health\n"
            "• High in resveratrol — heart-protective antioxidant\n"
            "• Good source of healthy monounsaturated fats\n"
            "• Filling and satisfying — helps with weight management\n\n"
            "💡 How to Use: Eat as a snack, add to chaat, make peanut butter, "
            "add to satay sauce, or sprinkle on salads."
        ),
        "pack_size": "250g",
        "image_urls": [
            "https://images.pexels.com/photos/4110380/pexels-photo-4110380.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    # ── BEVERAGES ─────────────────────────────────────────────────────────────
    {
        "keywords": ["mineral water", "drinking water", "bottled water"],
        "description": (
            "Pure natural mineral water from Himalayan springs. "
            "Naturally filtered through layers of ancient rock, enriched with "
            "essential minerals including calcium, magnesium, and potassium. "
            "No additives, no preservatives, just pure hydration.\n\n"
            "✅ Key Benefits:\n"
            "• Essential mineral replenishment\n"
            "• No artificial additives or preservatives\n"
            "• Supports healthy hydration throughout the day\n"
            "• Refreshing, clean taste\n\n"
            "💡 Serve chilled. Perfect for daily hydration, cooking, and healthy living."
        ),
        "pack_size": "1L",
        "image_urls": [
            "https://images.pexels.com/photos/1000084/pexels-photo-1000084.jpeg?auto=compress&cs=tinysrgb&w=600",
            "https://images.pexels.com/photos/3621584/pexels-photo-3621584.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["cold drink", "soda", "cola", "soft drink", "fanta", "sprite", "pepsi", "coca"],
        "description": (
            "Refreshing cold drinks — your favourite fizzy beverages. "
            "We stock a range of popular soft drinks and sodas. "
            "Perfect to quench thirst on a hot day or pair with your meals.\n\n"
            "✅ Available in multiple flavours\n"
            "• Serve chilled over ice for best taste\n"
            "• Best enjoyed fresh and cold\n\n"
            "💡 Keep refrigerated. Best consumed within the expiry date."
        ),
        "pack_size": "330ml",
        "image_urls": [
            "https://images.pexels.com/photos/1292116/pexels-photo-1292116.jpeg?auto=compress&cs=tinysrgb&w=600",
            "https://images.pexels.com/photos/50593/coca-cola-cold-drink-soft-drink-coke-50593.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["beer", "lager", "ale"],
        "description": (
            "Premium quality beer — chilled and refreshing. "
            "We stock popular domestic and imported beer brands. "
            "Best served ice cold at 4–6°C.\n\n"
            "✅ Refreshing taste · Chilled and ready\n\n"
            "⚠️ For adults 18+ only. Please drink responsibly. "
            "Do not drink and drive."
        ),
        "pack_size": "650ml",
        "image_urls": [
            "https://images.pexels.com/photos/1552630/pexels-photo-1552630.jpeg?auto=compress&cs=tinysrgb&w=600",
            "https://images.pexels.com/photos/544961/pexels-photo-544961.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["juice", "fruit juice", "orange juice", "mango juice"],
        "description": (
            "Fresh fruit juice — natural goodness in every sip. "
            "Made from real fruit with no artificial colours or flavours.\n\n"
            "✅ Natural fruit goodness · Rich in Vitamins · Refreshing taste\n\n"
            "💡 Shake well before serving. Refrigerate after opening."
        ),
        "pack_size": "250ml",
        "image_urls": [
            "https://images.pexels.com/photos/338713/pexels-photo-338713.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["tea", "chiya", "chai"],
        "description": (
            "Premium tea leaves — the Nepali way to start the day. "
            "Rich, aromatic, and full of natural antioxidants.\n\n"
            "✅ Key Benefits:\n"
            "• Rich in antioxidants\n"
            "• Natural energy boost\n"
            "• Aids digestion\n\n"
            "💡 Brew with hot water and milk for the classic Nepali chiya."
        ),
        "pack_size": "100g",
        "image_urls": [
            "https://images.pexels.com/photos/1638280/pexels-photo-1638280.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["coffee"],
        "description": (
            "Premium coffee — bold, rich, and aromatic. "
            "Sourced from high-altitude coffee farms.\n\n"
            "✅ Natural energy boost · Rich antioxidants · Improves focus\n\n"
            "💡 Brew with hot water or use an espresso machine."
        ),
        "pack_size": "100g",
        "image_urls": [
            "https://images.pexels.com/photos/302899/pexels-photo-302899.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    # ── GRAINS & PULSES ───────────────────────────────────────────────────────
    {
        "keywords": ["basmati", "basmati rice"],
        "description": (
            "Premium aged Basmati rice — the prince of rice. "
            "Sourced from the foothills of the Himalayas and aged for 12–24 months "
            "for perfect long, fluffy, non-sticky grains. Known worldwide for its "
            "distinctive floral aroma and nutty flavour.\n\n"
            "✅ Key Benefits:\n"
            "• Low glycaemic index (GI 50–58) — suitable for diabetics\n"
            "• Naturally gluten-free\n"
            "• Easy to digest — gentle on the stomach\n"
            "• High in carbohydrates — sustained energy release\n"
            "• Rich in vitamins B1, B6, and magnesium\n\n"
            "💡 Perfect for biryani, pulao, fried rice, dal-bhat, and as a "
            "plain steamed side dish. Rinse and soak for 30 mins before cooking."
        ),
        "pack_size": "1kg",
        "image_urls": [
            "https://images.pexels.com/photos/4110255/pexels-photo-4110255.jpeg?auto=compress&cs=tinysrgb&w=600",
            "https://images.pexels.com/photos/1586201375761/pexels-rice.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["rice", "chawal", "bhat"],
        "description": (
            "Quality rice — the staple of every Nepali meal. "
            "Our rice is carefully sorted and milled for consistent quality.\n\n"
            "✅ Gluten-free · Easy to digest · Good energy source\n\n"
            "💡 The foundation of dal-bhat — Nepal's national dish."
        ),
        "pack_size": "1kg",
        "image_urls": [
            "https://images.pexels.com/photos/4110255/pexels-photo-4110255.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["lentil", "dal", "daal", "masoor", "moong", "chana"],
        "description": (
            "Premium quality lentils and pulses — the protein backbone of Nepali cooking. "
            "Carefully cleaned and sorted for consistent quality.\n\n"
            "✅ Key Benefits:\n"
            "• Excellent plant-based protein source\n"
            "• High in dietary fibre\n"
            "• Rich in iron, folate, and potassium\n"
            "• Low glycaemic index — good for blood sugar control\n\n"
            "💡 The essential ingredient for dal — Nepal's national dish."
        ),
        "pack_size": "500g",
        "image_urls": [
            "https://images.pexels.com/photos/4110380/pexels-photo-4110380.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    # ── OILS ──────────────────────────────────────────────────────────────────
    {
        "keywords": ["mustard oil", "tori ko tel", "sarson"],
        "description": (
            "Pure cold-pressed mustard oil — the essential Nepali kitchen staple. "
            "Extracted by cold-pressing premium black mustard seeds, preserving all "
            "natural nutrients and the characteristic pungent aroma. "
            "Used in Nepali, Indian, and Bangladeshi cooking for centuries.\n\n"
            "✅ Key Benefits:\n"
            "• Rich in omega-3 and omega-6 fatty acids\n"
            "• Strong antibacterial and antifungal properties\n"
            "• Promotes hair growth and scalp health when used as oil\n"
            "• High smoke point (480°F) — excellent for deep frying\n"
            "• Contains allyl isothiocyanate — natural preservative\n\n"
            "💡 Use for tempering (chhounk), pickling, achar, hair oil, and deep frying. "
            "Traditionally used as a massage oil for newborns in Nepal."
        ),
        "pack_size": "1L",
        "image_urls": [
            "https://images.pexels.com/photos/4110371/pexels-photo-4110371.jpeg?auto=compress&cs=tinysrgb&w=600",
            "https://images.pexels.com/photos/725997/pexels-photo-725997.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["sunflower oil", "vegetable oil", "cooking oil", "tel"],
        "description": (
            "Premium cooking oil — light, neutral, and healthy. "
            "Ideal for everyday frying, sautéing, and baking.\n\n"
            "✅ Light taste · High smoke point · Heart-healthy fats\n\n"
            "💡 Suitable for all types of cooking."
        ),
        "pack_size": "1L",
        "image_urls": [
            "https://images.pexels.com/photos/725997/pexels-photo-725997.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    # ── SNACKS ────────────────────────────────────────────────────────────────
    {
        "keywords": ["wai wai", "noodle", "instant noodle", "chowmein", "ramen"],
        "description": (
            "Wai Wai instant noodles — Nepal's most iconic snack since 1984. "
            "The original Nepali instant noodle that can be eaten two ways: "
            "dry and crunchy straight from the pack, or cooked as a hot soup. "
            "Available in chicken, vegetable, and masala flavours.\n\n"
            "✅ Key Features:\n"
            "• Ready in under 3 minutes — perfect quick meal\n"
            "• Eat dry as a crunchy snack or cook as noodle soup\n"
            "• Available in multiple flavours\n"
            "• Loved by all ages across Nepal\n\n"
            "💡 To cook: boil 1.5 cups water, add noodles and spice mix, "
            "cook 2–3 minutes. To eat dry: crush in pack, add spice mix and shake."
        ),
        "pack_size": "75g",
        "image_urls": [
            "https://images.pexels.com/photos/1279330/pexels-photo-1279330.jpeg?auto=compress&cs=tinysrgb&w=600",
            "https://images.pexels.com/photos/884600/pexels-photo-884600.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["biscuit", "cookie", "cracker"],
        "description": (
            "Crispy biscuits and cookies — the perfect tea-time companion. "
            "Light, crunchy, and delicious.\n\n"
            "✅ Great with tea or coffee · Light snack · Various flavours\n\n"
            "💡 Best enjoyed with a hot cup of chiya."
        ),
        "pack_size": "100g",
        "image_urls": [
            "https://images.pexels.com/photos/1028714/pexels-photo-1028714.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["chips", "crisps", "kurkure", "snack"],
        "description": (
            "Crunchy snacks — the perfect anytime munchie. "
            "Crispy, flavourful, and satisfying.\n\n"
            "✅ Crispy texture · Bold flavours · Great for sharing\n\n"
            "💡 Best enjoyed chilled or at room temperature."
        ),
        "pack_size": "50g",
        "image_urls": [
            "https://images.pexels.com/photos/1583884/pexels-photo-1583884.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    # ── DAIRY ─────────────────────────────────────────────────────────────────
    {
        "keywords": ["milk", "dudh", "dahi", "yogurt", "curd", "paneer"],
        "description": (
            "Fresh dairy products — rich and nutritious. "
            "Sourced from local farms for maximum freshness.\n\n"
            "✅ Rich in calcium · High protein · Essential vitamins\n\n"
            "💡 Keep refrigerated and consume before expiry date."
        ),
        "pack_size": "500ml",
        "image_urls": [
            "https://images.pexels.com/photos/248412/pexels-photo-248412.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    # ── SPICES ────────────────────────────────────────────────────────────────
    {
        "keywords": ["spice", "masala", "turmeric", "haldi", "cumin", "jeera",
                     "coriander", "dhania", "chilli", "pepper", "garam masala"],
        "description": (
            "Premium quality spices — the soul of Nepali cooking. "
            "Freshly ground or whole, our spices are sourced from trusted farms "
            "for maximum aroma and flavour.\n\n"
            "✅ Full aroma and flavour · No artificial additives · Freshly packed\n\n"
            "💡 Store in airtight containers away from heat and light."
        ),
        "pack_size": "100g",
        "image_urls": [
            "https://images.pexels.com/photos/1340116/pexels-photo-1340116.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    # ── PERSONAL CARE ─────────────────────────────────────────────────────────
    {
        "keywords": ["toothpaste", "colgate", "sensodyne", "oral", "dental"],
        "description": (
            "Premium toothpaste — trusted by dentists worldwide. "
            "Advanced fluoride formula provides all-round protection against "
            "cavities, plaque, and bad breath. Regular use maintains strong "
            "enamel and healthy gums.\n\n"
            "✅ Key Benefits:\n"
            "• Clinically proven cavity protection\n"
            "• Freshens breath for 12+ hours\n"
            "• Gently whitens teeth with regular use\n"
            "• Strengthens tooth enamel\n"
            "• Fights harmful bacteria\n\n"
            "💡 Brush twice daily for 2 minutes. Use a soft-bristle toothbrush "
            "for best results. Recommended by dentists."
        ),
        "pack_size": "150g",
        "image_urls": [
            "https://images.pexels.com/photos/3762875/pexels-photo-3762875.jpeg?auto=compress&cs=tinysrgb&w=600",
            "https://images.pexels.com/photos/3762879/pexels-photo-3762879.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["shampoo", "hair wash", "conditioner", "hair oil"],
        "description": (
            "Premium hair care — for healthy, strong, shiny hair. "
            "Gently cleanses the scalp and hair, removing dirt, oil, and buildup "
            "without stripping natural moisture.\n\n"
            "✅ Key Benefits:\n"
            "• Gentle cleansing for all hair types\n"
            "• Adds natural shine and reduces frizz\n"
            "• Strengthens hair from root to tip\n"
            "• Pleasant long-lasting fragrance\n"
            "• Suitable for daily use\n\n"
            "💡 Apply to wet hair, lather thoroughly, rinse well. "
            "Follow with conditioner for best results."
        ),
        "pack_size": "200ml",
        "image_urls": [
            "https://images.pexels.com/photos/3735149/pexels-photo-3735149.jpeg?auto=compress&cs=tinysrgb&w=600",
            "https://images.pexels.com/photos/1662519/pexels-photo-1662519.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["soap", "sabun", "body wash", "hand wash"],
        "description": (
            "Premium soap — gentle and effective cleansing. "
            "Moisturising formula keeps skin soft and hydrated.\n\n"
            "✅ Gentle formula · Long-lasting fragrance · Moisturising\n\n"
            "💡 Lather well with water, rinse thoroughly."
        ),
        "pack_size": "100g",
        "image_urls": [
            "https://images.pexels.com/photos/2113855/pexels-photo-2113855.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["lotion", "cream", "moisturiser", "sunscreen"],
        "description": (
            "Premium skin care — nourish and protect your skin. "
            "Enriched with vitamins and moisturising agents.\n\n"
            "✅ Deep moisturising · Non-greasy · Suitable for all skin types\n\n"
            "💡 Apply to clean skin morning and evening."
        ),
        "pack_size": "200ml",
        "image_urls": [
            "https://images.pexels.com/photos/3735641/pexels-photo-3735641.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    # ── HOUSEHOLD ─────────────────────────────────────────────────────────────
    {
        "keywords": ["detergent", "washing powder", "surf", "ariel", "tide"],
        "description": (
            "Premium laundry detergent — sparkling clean clothes every time. "
            "Advanced formula removes tough stains while being gentle on fabric.\n\n"
            "✅ Powerful stain removal · Fresh fragrance · Colour-safe\n\n"
            "💡 Use as directed on packaging. Keep away from children."
        ),
        "pack_size": "500g",
        "image_urls": [
            "https://images.pexels.com/photos/4239091/pexels-photo-4239091.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
    {
        "keywords": ["tissue", "toilet paper", "napkin", "towel"],
        "description": (
            "Soft and strong tissue paper — gentle and absorbent. "
            "Multi-ply construction for superior softness.\n\n"
            "✅ Soft texture · Strong and absorbent · Hygienic packaging\n\n"
            "💡 Store in a dry place."
        ),
        "pack_size": "Pack of 6",
        "image_urls": [
            "https://images.pexels.com/photos/3737598/pexels-photo-3737598.jpeg?auto=compress&cs=tinysrgb&w=600",
        ],
    },
]

# ── Default fallback ──────────────────────────────────────────────────────────
DEFAULT = {
    "description": (
        "Quality product available at GoldKernel store. "
        "We carefully select all our products to ensure the best quality "
        "for our customers in Nepal.\n\n"
        "✅ Genuine product · Competitive price · Fast delivery across Nepal\n\n"
        "💡 Contact us for more details about this product."
    ),
    "pack_size": None,
    "image_urls": [
        "https://images.pexels.com/photos/5632388/pexels-photo-5632388.jpeg?auto=compress&cs=tinysrgb&w=600",
    ],
}


# ── Core matching function ────────────────────────────────────────────────────

def find_match(product_name: str, category: str = "") -> dict:
    """Return best catalogue entry for product name + category."""
    text = (product_name + " " + (category or "")).lower()
    best = None
    best_score = 0

    for entry in CATALOGUE:
        score = 0
        for kw in entry["keywords"]:
            if kw in text:
                score += len(kw)  # longer keyword = more specific = higher score
        if score > best_score:
            best_score = score
            best = entry

    return best if best else DEFAULT


def _safe_filename(product_id: int, name: str) -> str:
    safe = re.sub(r"[^\w]", "_", name.lower())[:40]
    return f"product_{product_id}_{safe}.jpg"


# ── Main public function ──────────────────────────────────────────────────────

def autofill_product(product, force: bool = False) -> dict:
    """
    Auto-fill description, pack_size, and image for a product.
    Only fills fields that are empty (unless force=True).

    Returns dict of field names that were updated.
    """
    from ..extensions import db

    name = product.name or ""
    category = product.category or ""
    data = find_match(name, category)
    updated = {}

    # Description
    if force or not product.description:
        product.description = data["description"]
        updated["description"] = True

    # Pack size
    if (force or not getattr(product, "pack_size", None)) and data.get("pack_size"):
        product.pack_size = data["pack_size"]
        updated["pack_size"] = True

    # Image — download and save
    if force or not product.image_filename:
        filename = _safe_filename(product.id, name)
        downloaded = False
        for url in data["image_urls"]:
            if _download_image(url, filename):
                downloaded = True
                break
        if downloaded:
            product.image_filename = filename
            updated["image_filename"] = True

    # Slug (for SEO URLs)
    if force or not getattr(product, "slug", None):
        try:
            raw = re.sub(r"[^\w\s-]", "", name.lower()).strip()
            slug_candidate = re.sub(r"[\s_-]+", "-", raw)[:120]
            if slug_candidate:
                from ..models.product import Product
                conflict = db.session.execute(
                    db.select(Product).where(
                        Product.slug == slug_candidate,
                        Product.id != product.id,
                    )
                ).scalar_one_or_none()
                if not conflict:
                    product.slug = slug_candidate
                    updated["slug"] = True
        except Exception:
            pass

    if updated:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    return updated
