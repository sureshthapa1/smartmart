"""Promotion service — CRUD and discount calculation (Feature #6)."""
from __future__ import annotations

from datetime import date

from ..extensions import db
from ..models.promotion import Promotion


def list_promotions(active_only: bool = False) -> list[Promotion]:
    stmt = db.select(Promotion).order_by(Promotion.start_date.desc())
    if active_only:
        today = date.today()
        stmt = stmt.where(
            Promotion.is_active == True,
            Promotion.start_date <= today,
            Promotion.end_date >= today,
        )
    return db.session.execute(stmt).scalars().all()


def get_active_promotions_for_cart(items: list[dict], subtotal: float) -> list[dict]:
    """Return applicable promotions and their discount amounts for a cart."""
    today = date.today()
    promos = db.session.execute(
        db.select(Promotion).where(
            Promotion.is_active == True,
            Promotion.start_date <= today,
            Promotion.end_date >= today,
        )
    ).scalars().all()

    results = []
    for promo in promos:
        if promo.min_purchase and subtotal < float(promo.min_purchase):
            continue
        discount = promo.calculate_discount(subtotal)
        if discount > 0:
            results.append({
                "id": promo.id,
                "name": promo.name,
                "type": promo.promo_type,
                "discount": discount,
            })
    return results


def create_promotion(data: dict) -> Promotion:
    promo = Promotion(
        name=data["name"],
        promo_type=data.get("promo_type", "percentage"),
        discount_value=data.get("discount_value", 0),
        buy_qty=data.get("buy_qty"),
        free_qty=data.get("free_qty"),
        scope=data.get("scope", "all"),
        scope_value=data.get("scope_value"),
        min_purchase=data.get("min_purchase"),
        start_date=data["start_date"],
        end_date=data["end_date"],
        is_active=data.get("is_active", True),
        created_by=data["created_by"],
    )
    db.session.add(promo)
    db.session.commit()
    return promo


def update_promotion(promo_id: int, data: dict) -> Promotion:
    promo = db.get_or_404(Promotion, promo_id)
    for field in ("name", "promo_type", "discount_value", "buy_qty", "free_qty",
                  "scope", "scope_value", "min_purchase", "start_date", "end_date", "is_active"):
        if field in data:
            setattr(promo, field, data[field])
    db.session.commit()
    return promo


def delete_promotion(promo_id: int) -> None:
    promo = db.get_or_404(Promotion, promo_id)
    db.session.delete(promo)
    db.session.commit()
