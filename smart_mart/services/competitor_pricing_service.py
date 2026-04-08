from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from ..extensions import db
from ..models.ai_enhancements import CompetitorPriceEntry, CompetitorPriceSuggestion
from ..models.product import Product
from .ai_decision_logger import log_decision


def add_competitor_price(
    product_id: int,
    competitor_name: str,
    competitor_price: float,
    captured_by_user_id: int | None = None,
    notes: str | None = None,
) -> CompetitorPriceEntry:
    entry = CompetitorPriceEntry(
        product_id=product_id,
        competitor_name=competitor_name.strip(),
        competitor_price=Decimal(str(competitor_price)),
        observed_at=datetime.now(timezone.utc),
        captured_by_user_id=captured_by_user_id,
        notes=(notes or "").strip() or None,
    )
    db.session.add(entry)
    db.session.commit()
    return entry


def compare_product_price(product_id: int) -> dict:
    product = db.get_or_404(Product, product_id)
    entries = db.session.execute(
        db.select(CompetitorPriceEntry)
        .where(CompetitorPriceEntry.product_id == product_id)
        .order_by(CompetitorPriceEntry.observed_at.desc())
    ).scalars().all()
    comparisons = []
    for entry in entries:
        diff = float(product.selling_price) - float(entry.competitor_price)
        pct = (diff / float(entry.competitor_price) * 100) if float(entry.competitor_price) > 0 else 0
        comparisons.append(
            {
                "entry_id": entry.id,
                "competitor_name": entry.competitor_name,
                "competitor_price": float(entry.competitor_price),
                "our_price": float(product.selling_price),
                "difference": round(diff, 2),
                "difference_pct": round(pct, 2),
                "observed_at": entry.observed_at.isoformat() if entry.observed_at else None,
            }
        )
    return {
        "product_id": product.id,
        "product_name": product.name,
        "our_price": float(product.selling_price),
        "comparisons": comparisons,
    }


def generate_pricing_suggestion(product_id: int) -> dict:
    product = db.get_or_404(Product, product_id)
    latest = db.session.execute(
        db.select(CompetitorPriceEntry)
        .where(CompetitorPriceEntry.product_id == product_id)
        .order_by(CompetitorPriceEntry.observed_at.desc())
    ).scalars().first()
    if latest is None:
        raise ValueError("No competitor price found for this product.")

    cost_price = float(product.cost_price)
    our_price = float(product.selling_price)
    competitor_price = float(latest.competitor_price)
    min_safe_price = round(cost_price * 1.08, 2)

    if competitor_price < our_price and competitor_price >= min_safe_price:
        suggested = round((competitor_price + our_price) / 2, 2)
        rationale = "Competitor is cheaper; reduce modestly while protecting margin."
        confidence = 0.82
    elif competitor_price > our_price * 1.07:
        suggested = round(min(competitor_price * 0.98, our_price * 1.04), 2)
        suggested = max(suggested, min_safe_price)
        rationale = "Market has room to increase price with low undercut risk."
        confidence = 0.74
    else:
        suggested = our_price
        rationale = "Current price is already aligned with market."
        confidence = 0.68

    suggestion = CompetitorPriceSuggestion(
        product_id=product.id,
        competitor_entry_id=latest.id,
        current_price=our_price,
        suggested_price=suggested,
        confidence=confidence,
        rationale=rationale,
    )
    db.session.add(suggestion)

    log_decision(
        decision_type="competitor_pricing_suggestion",
        entity_type="product",
        entity_id=product.id,
        input_snapshot={
            "our_price": our_price,
            "competitor_price": competitor_price,
            "cost_price": cost_price,
        },
        output_snapshot={
            "suggested_price": suggested,
            "rationale": rationale,
        },
        confidence=confidence,
    )
    db.session.commit()
    return {
        "product_id": product.id,
        "product_name": product.name,
        "current_price": our_price,
        "suggested_price": float(suggested),
        "confidence": float(confidence),
        "rationale": rationale,
        "competitor_price": competitor_price,
        "competitor_name": latest.competitor_name,
    }
