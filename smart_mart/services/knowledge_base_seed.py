"""Seed default FAQ articles for the store chatbot knowledge base."""
from __future__ import annotations


DEFAULT_ARTICLES = [
    {
        "title": "Delivery Policy",
        "category": "delivery",
        "keywords": "delivery,deliver,shipping,ship,dispatch,arrive,when,how long,days",
        "body": (
            "GoldKernel delivers across all of Nepal. "
            "Delivery within Kathmandu Valley takes 1-2 business days. "
            "Outside the valley takes 3-5 business days depending on your district. "
            "Free delivery on orders above NPR 2000. "
            "NPR 100 flat delivery charge on orders below NPR 2000. "
            "We use trusted courier partners for outside-valley deliveries."
        ),
    },
    {
        "title": "Return & Refund Policy",
        "category": "returns",
        "keywords": "return,refund,exchange,replace,damaged,wrong,problem,issue,complaint",
        "body": (
            "We offer a 7-day return policy. "
            "If you receive a damaged, wrong, or expired product, "
            "please WhatsApp us within 7 days of delivery with a photo. "
            "We'll arrange a free replacement or full refund within 3-5 business days. "
            "For COD orders, refunds are processed via eSewa or bank transfer. "
            "Products must be in original packaging for returns."
        ),
    },
    {
        "title": "Payment Methods",
        "category": "payment",
        "keywords": "payment,pay,esewa,khalti,cod,cash,online,how to pay,payment method",
        "body": (
            "We accept: Cash on Delivery (COD), eSewa, and Khalti. "
            "COD: Pay cash when your order arrives. "
            "eSewa/Khalti: Pay online at checkout for instant confirmation. "
            "For online payments, you'll be redirected to the payment page after placing your order. "
            "All online transactions are secure and encrypted."
        ),
    },
    {
        "title": "Product Quality & Freshness",
        "category": "quality",
        "keywords": "fresh,quality,expiry,expire,organic,natural,pure,original,authentic",
        "body": (
            "All GoldKernel dry fruits are handpicked and sourced directly from trusted suppliers. "
            "We maintain strict quality control — products are checked for freshness before packing. "
            "Expiry dates are printed on all packaging. "
            "Our almonds are sourced from California and Kashmir; cashews from India; "
            "walnuts from Himachal Pradesh. "
            "We do not use artificial preservatives or colors."
        ),
    },
    {
        "title": "Order Tracking",
        "category": "tracking",
        "keywords": "track,tracking,order status,where is my order,locate order,find order",
        "body": (
            "Track your order at goldkernel.com/store/track. "
            "Enter your order number (starts with GK-) or your phone number. "
            "You'll see your order status: Confirmed, Processing, Shipped, or Delivered. "
            "For COD orders, our team will call before delivery. "
            "For questions, WhatsApp us with your order number."
        ),
    },
    {
        "title": "Loyalty Points Program",
        "category": "loyalty",
        "keywords": "loyalty,points,reward,discount,earn,redeem,wallet,cashback",
        "body": (
            "Earn loyalty points on every purchase at GoldKernel! "
            "You earn points equivalent to 1% of your order value. "
            "Points can be redeemed at checkout for discounts — 1 point = NPR 1. "
            "Check your points balance in your account page. "
            "Points expire after 12 months of inactivity. "
            "Loyalty points work on all products and payment methods."
        ),
    },
    {
        "title": "Gift Packaging & Corporate Orders",
        "category": "gifting",
        "keywords": "gift,wrap,gifting,corporate,bulk,hamper,box,present,dashain,tihar,wedding",
        "body": (
            "GoldKernel offers premium gift wrapping for NPR 50 per order — "
            "add it at checkout! "
            "Perfect for Dashain, Tihar, weddings, and corporate gifting. "
            "For bulk corporate orders (NPR 10,000+), contact us on WhatsApp for "
            "custom packaging, branding, and special pricing. "
            "We can deliver branded gift boxes to your office or event venue."
        ),
    },
    {
        "title": "Nutritional Information & Health Benefits",
        "category": "nutrition",
        "keywords": "nutrition,calories,protein,healthy,health,diet,diabetic,weight,vitamin,benefit",
        "body": (
            "Dry fruits are excellent sources of protein, healthy fats, fiber, and micronutrients. "
            "Almonds: high in Vitamin E, magnesium, and healthy fats — great for brain and heart. "
            "Walnuts: rich in Omega-3 fatty acids — support brain health. "
            "Cashews: high in iron and zinc — good for immunity. "
            "Dates: natural energy source, high in fiber — good for digestion. "
            "Raisins: rich in antioxidants and iron. "
            "For diabetics, we recommend almonds, walnuts, and pistachios in moderation."
        ),
    },
]


def seed_knowledge_base() -> int:
    """Insert default articles if the table is empty. Returns count inserted."""
    from ..extensions import db
    from ..models.knowledge_article import KnowledgeArticle

    existing = db.session.execute(
        db.select(db.func.count(KnowledgeArticle.id))
    ).scalar() or 0

    if existing >= len(DEFAULT_ARTICLES):
        return 0   # Already seeded

    inserted = 0
    for art_data in DEFAULT_ARTICLES:
        exists = db.session.execute(
            db.select(KnowledgeArticle).where(KnowledgeArticle.title == art_data["title"])
        ).scalar_one_or_none()
        if not exists:
            db.session.add(KnowledgeArticle(**art_data))
            inserted += 1

    if inserted:
        db.session.commit()

    return inserted
