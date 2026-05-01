"""Offer Service — core business logic for the Customer Retention & Offer System.

Responsibilities:
  - Fetch active offers for a customer (fast, non-blocking)
  - Apply an offer to a sale (validate + discount)
  - Rollback an offer when a sale is cancelled
  - Assign an offer to a customer after billing
  - Auto-expire stale offers
  - AI-driven offer suggestions based on customer behaviour
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import func

from ..extensions import db
from ..models.offer import Offer, CustomerOffer, OfferNotification

logger = logging.getLogger(__name__)

# Max discount values to prevent runaway discounts
_MAX_PERCENTAGE = 100.0
_MAX_FIXED_NPR = 100_000.0


# ── Public API ────────────────────────────────────────────────────────────────

def get_active_offers_for_customer(customer_id: int) -> list[dict]:
    """Return a list of valid (unused, non-expired) offers for a customer.

    Designed to be called during billing — returns lightweight dicts so the
    billing UI can render the offer banner without a blocking DB round-trip.
    Fails gracefully — returns [] on any DB error so billing is never blocked.
    """
    try:
        today = date.today()
        rows = db.session.execute(
            db.select(CustomerOffer, Offer)
            .join(Offer, Offer.id == CustomerOffer.offer_id)
            .where(
                CustomerOffer.customer_id == customer_id,
                CustomerOffer.status == CustomerOffer.STATUS_UNUSED,
                CustomerOffer.expiry_date >= today,
                Offer.status == "active",
            )
            .order_by(Offer.discount_value.desc())
        ).all()

        result = []
        for co, offer in rows:
            result.append({
                "customer_offer_id": co.id,
                "offer_id": offer.id,
                "title": offer.title,
                "description": offer.description or "",
                "offer_type": offer.offer_type,
                "type_label": offer.type_label,
                "discount_value": float(offer.discount_value),
                "min_purchase_amount": float(offer.min_purchase_amount or 0),
                "product_id": offer.product_id,
                "expiry_date": co.expiry_date.isoformat(),
                "days_until_expiry": co.days_until_expiry,
                "usage_count": co.usage_count,
                "usage_limit": offer.usage_limit,
            })
        return result
    except Exception as exc:
        logger.warning("get_active_offers_for_customer failed (non-fatal): %s", exc)
        return []


def get_best_offer_for_customer(customer_id: int, cart_total: float) -> Optional[dict]:
    """Return the single best applicable offer for a given cart total."""
    offers = get_active_offers_for_customer(customer_id)
    best = None
    best_discount = 0.0
    for o in offers:
        if cart_total < o["min_purchase_amount"]:
            continue
        # Estimate discount
        if o["offer_type"] == "percentage":
            disc = cart_total * o["discount_value"] / 100
        elif o["offer_type"] in ("fixed", "combo"):
            disc = min(o["discount_value"], cart_total)
        elif o["offer_type"] == "conditional":
            disc = cart_total * o["discount_value"] / 100 if o["discount_value"] <= 100 else min(o["discount_value"], cart_total)
        else:
            disc = 0.0
        if disc > best_discount:
            best_discount = disc
            best = {**o, "estimated_discount": round(disc, 2)}
    return best


def apply_offer(
    customer_offer_id: int,
    sale_id: int,
    cart_total: float,
    product_subtotal: float = 0.0,
    customer_id: int | None = None,
) -> dict:
    """Validate and apply a customer offer to a sale.

    Returns a dict with:
      - discount_amount: float
      - message: str
    Raises ValueError on validation failure.

    Pass customer_id to enforce ownership — prevents one customer using
    another customer's offer.
    """
    co = db.session.get(CustomerOffer, customer_offer_id)
    if not co:
        raise ValueError("Offer assignment not found.")

    # Ownership check — ensure offer belongs to the right customer
    if customer_id and co.customer_id != customer_id:
        logger.warning(
            "Offer ownership mismatch: co.customer_id=%d, requested customer_id=%d",
            co.customer_id, customer_id,
        )
        raise ValueError("This offer does not belong to the selected customer.")

    offer = co.offer
    today = date.today()

    # Validation
    if co.status == CustomerOffer.STATUS_USED:
        raise ValueError("This offer has already been used.")
    if co.status == CustomerOffer.STATUS_EXPIRED or co.expiry_date < today:
        co.status = CustomerOffer.STATUS_EXPIRED
        db.session.commit()
        raise ValueError("This offer has expired.")
    if co.usage_count >= offer.usage_limit:
        raise ValueError(f"Offer usage limit ({offer.usage_limit}) reached.")
    if offer.status != "active":
        raise ValueError("This offer is no longer active.")

    min_amt = float(offer.min_purchase_amount or 0)
    if min_amt and cart_total < min_amt:
        raise ValueError(
            f"Minimum purchase of NPR {min_amt:,.2f} required for this offer "
            f"(cart total: NPR {cart_total:,.2f})."
        )

    # Calculate discount
    discount = offer.calculate_discount(cart_total, product_subtotal)

    # Mark as used
    co.status = CustomerOffer.STATUS_USED
    co.usage_count += 1
    co.applied_sale_id = sale_id
    db.session.commit()

    logger.info(
        "Offer #%d applied to sale #%d for customer #%d — discount NPR %.2f",
        offer.id, sale_id, co.customer_id, discount,
    )
    return {
        "discount_amount": discount,
        "message": f"✅ Offer '{offer.title}' applied — NPR {discount:,.2f} off",
    }


def rollback_offer(sale_id: int) -> bool:
    """Revert any offer applied to a sale (called when sale is cancelled/deleted).

    Returns True if an offer was reverted, False if none was applied.
    """
    co = db.session.execute(
        db.select(CustomerOffer).where(CustomerOffer.applied_sale_id == sale_id)
    ).scalar_one_or_none()

    if not co:
        return False

    co.status = CustomerOffer.STATUS_UNUSED
    co.usage_count = max(0, co.usage_count - 1)
    co.applied_sale_id = None
    db.session.commit()
    logger.info("Offer #%d reverted for sale #%d", co.offer_id, sale_id)
    return True


def assign_offer_to_customer(
    customer_id: int,
    offer_id: int,
    assigned_at_sale_id: int | None = None,
    send_notification: bool = True,
) -> CustomerOffer:
    """Assign an offer to a customer (e.g. after billing for next visit).

    Prevents duplicate active assignments for the same offer.
    Returns the CustomerOffer (new or existing).
    """
    today = date.today()

    # Check for existing active assignment (idempotent)
    existing = db.session.execute(
        db.select(CustomerOffer).where(
            CustomerOffer.customer_id == customer_id,
            CustomerOffer.offer_id == offer_id,
            CustomerOffer.status == CustomerOffer.STATUS_UNUSED,
            CustomerOffer.expiry_date >= today,
        )
    ).scalar_one_or_none()

    if existing:
        logger.info(
            "Customer #%d already has active offer #%d (customer_offer #%d) — skipping duplicate",
            customer_id, offer_id, existing.id,
        )
        existing._is_duplicate = True  # type: ignore[attr-defined]
        return existing

    co = CustomerOffer.create_for_customer(
        customer_id=customer_id,
        offer_id=offer_id,
        assigned_at_sale_id=assigned_at_sale_id,
    )
    co._is_duplicate = False  # type: ignore[attr-defined]
    db.session.commit()

    if send_notification:
        try:
            _send_offer_assigned_notification(co)
        except Exception as exc:
            logger.warning("Offer notification failed (non-fatal): %s", exc)

    return co


def expire_stale_offers() -> int:
    """Mark all past-expiry unused offers as expired. Returns count updated."""
    today = date.today()
    result = db.session.execute(
        db.update(CustomerOffer)
        .where(
            CustomerOffer.status == CustomerOffer.STATUS_UNUSED,
            CustomerOffer.expiry_date < today,
        )
        .values(status=CustomerOffer.STATUS_EXPIRED)
    )
    db.session.commit()
    count = result.rowcount
    if count:
        logger.info("Auto-expired %d stale customer offers", count)
    return count


def send_expiry_reminders() -> dict:
    """Send reminders for offers expiring in 1 or 2 days. Returns counts."""
    today = date.today()
    counts = {"2d": 0, "1d": 0, "failed": 0}

    for days_ahead, notif_type in [(2, "reminder_2d"), (1, "reminder_1d")]:
        target_date = today + timedelta(days=days_ahead)
        cos = db.session.execute(
            db.select(CustomerOffer)
            .where(
                CustomerOffer.status == CustomerOffer.STATUS_UNUSED,
                CustomerOffer.expiry_date == target_date,
            )
        ).scalars().all()

        for co in cos:
            # Skip if already sent this reminder type
            already_sent = db.session.execute(
                db.select(OfferNotification).where(
                    OfferNotification.customer_offer_id == co.id,
                    OfferNotification.notification_type == notif_type,
                    OfferNotification.status.in_(["sent", "delivered"]),
                )
            ).scalar_one_or_none()
            if already_sent:
                continue

            try:
                _send_offer_reminder_notification(co, notif_type, days_ahead)
                counts["2d" if days_ahead == 2 else "1d"] += 1
            except Exception as exc:
                logger.warning("Reminder notification failed for co #%d: %s", co.id, exc)
                counts["failed"] += 1

    return counts


def retry_failed_notifications(max_retries: int = 3) -> int:
    """Retry failed offer notifications with exponential backoff. Returns count retried."""
    import time as _time

    failed = db.session.execute(
        db.select(OfferNotification).where(
            OfferNotification.status == "failed",
            OfferNotification.retry_count < max_retries,
        )
    ).scalars().all()

    retried = 0
    for notif in failed:
        try:
            co = notif.customer_offer
            if not co:
                logger.warning("Notification #%d has no customer_offer — skipping", notif.id)
                notif.retry_count = max_retries  # don't retry again
                db.session.commit()
                continue
            customer = co.customer
            if not customer or not customer.phone:
                notif.retry_count = max_retries
                db.session.commit()
                continue

            # Exponential backoff: wait 2^retry_count seconds (capped at 60s)
            backoff = min(2 ** notif.retry_count, 60)
            _time.sleep(backoff)

            msg = _build_offer_message(co, notif.notification_type)
            from .notification_service import send_notification
            log = send_notification(customer.phone, msg, channel=notif.channel)
            notif.status = log.status
            notif.provider_ref = log.provider_ref
            notif.error = log.error
            notif.retry_count += 1
            notif.sent_at = log.sent_at
            db.session.commit()
            retried += 1
        except Exception as exc:
            logger.warning("Retry failed for notification #%d: %s", notif.id, exc)
            try:
                notif.retry_count += 1
                db.session.commit()
            except Exception:
                db.session.rollback()

    return retried


# ── AI Smart Offer Engine ─────────────────────────────────────────────────────

def ai_suggest_offers_for_customer(customer_id: int) -> list[dict]:
    """Generate AI-driven offer suggestions based on customer behaviour.

    Rules:
      - No visit in 14+ days → comeback offer (15% off)
      - High spender (1.5× average) → premium offer (10% off)
      - Low visit frequency (< 2 visits in 90 days) → incentive offer (NPR 50 off)
      - Birthday within 7 days → birthday offer (20% off)
    """
    from ..models.customer import Customer
    from ..models.sale import Sale

    try:
        customer = db.session.get(Customer, customer_id)
        if not customer:
            return []

        suggestions = []
        today = date.today()

        # ── Days since last visit ─────────────────────────────────────────
        last_visit = customer.last_visit
        if last_visit:
            lv_date = last_visit.date() if hasattr(last_visit, "date") else last_visit
            days_since = (today - lv_date).days
        else:
            days_since = 999

        # ── Total spent (use customer_id FK when available, fall back to name) ──
        total_spent_row = db.session.execute(
            db.select(func.sum(Sale.total_amount))
            .where(Sale.customer_id == customer_id)
        ).scalar()
        if total_spent_row is None:
            # Fall back to name-based match for older sales without customer_id
            total_spent_row = db.session.execute(
                db.select(func.sum(Sale.total_amount))
                .where(func.lower(Sale.customer_name) == customer.name.lower())
            ).scalar() or 0
        total_spent = float(total_spent_row or 0)

        # ── Average total spent across all customers ──────────────────────
        # Use a simpler, more reliable query
        avg_row = db.session.execute(
            db.select(func.avg(Sale.total_amount))
        ).scalar() or 1
        # Multiply by avg visits to get per-customer estimate
        avg_visits_row = db.session.execute(
            db.select(func.avg(Customer.visit_count))
        ).scalar() or 1
        avg_customer_spent = float(avg_row) * float(avg_visits_row)
        if avg_customer_spent < 1:
            avg_customer_spent = 1.0

        # ── Visit frequency (last 90 days) ────────────────────────────────
        ninety_days_ago = datetime.now(timezone.utc) - timedelta(days=90)
        recent_visits = db.session.execute(
            db.select(func.count(Sale.id))
            .where(
                Sale.customer_id == customer_id,
                Sale.sale_date >= ninety_days_ago,
            )
        ).scalar() or 0
        # Also check by name for older sales
        if recent_visits == 0:
            recent_visits = db.session.execute(
                db.select(func.count(Sale.id))
                .where(
                    func.lower(Sale.customer_name) == customer.name.lower(),
                    Sale.sale_date >= ninety_days_ago,
                )
            ).scalar() or 0

        # ── Birthday check (fixed: use <= for today's birthday) ───────────
        birthday_soon = False
        days_to_bday = None
        if customer.birthday:
            bday_this_year = customer.birthday.replace(year=today.year)
            if bday_this_year <= today:  # already passed this year (or today)
                bday_this_year = customer.birthday.replace(year=today.year + 1)
            days_to_bday = (bday_this_year - today).days
            birthday_soon = 0 <= days_to_bday <= 7

        # ── Rule 1: Comeback offer ────────────────────────────────────────
        if days_since >= 14:
            suggestions.append({
                "type": "comeback",
                "title": "We Miss You! 15% Off",
                "description": (
                    f"It's been {days_since} days since your last visit. "
                    "Come back and enjoy 15% off your next purchase!"
                ),
                "offer_type": "percentage",
                "discount_value": 15.0,
                "min_purchase_amount": 0,
                "valid_days": 7,
                "reason": f"No visit in {days_since} days",
            })

        # ── Rule 2: High spender premium offer ────────────────────────────
        if total_spent > avg_customer_spent * 1.5 and total_spent > 1000:
            suggestions.append({
                "type": "premium",
                "title": "VIP Customer — 10% Off",
                "description": (
                    "As one of our top customers, enjoy an exclusive "
                    "10% discount on your next visit."
                ),
                "offer_type": "percentage",
                "discount_value": 10.0,
                "min_purchase_amount": 500,
                "valid_days": 14,
                "reason": f"High spender (NPR {total_spent:,.0f} total)",
            })

        # ── Rule 3: Low frequency incentive ──────────────────────────────
        # < 2 visits in last 90 days AND customer is not brand new (> 30 days old)
        customer_age_days = (today - customer.created_at.date()).days if customer.created_at else 0
        if recent_visits < 2 and customer_age_days > 30 and days_since < 60:
            suggestions.append({
                "type": "incentive",
                "title": "Shop More — NPR 50 Off",
                "description": "Visit us more often and save! Get NPR 50 off on purchases above NPR 300.",
                "offer_type": "fixed",
                "discount_value": 50.0,
                "min_purchase_amount": 300,
                "valid_days": 10,
                "reason": f"Only {recent_visits} visit(s) in last 90 days",
            })

        # ── Rule 4: Birthday offer ────────────────────────────────────────
        if birthday_soon and days_to_bday is not None:
            day_str = "today" if days_to_bday == 0 else f"in {days_to_bday} day(s)"
            suggestions.append({
                "type": "birthday",
                "title": "🎂 Happy Birthday — 20% Off",
                "description": "Wishing you a wonderful birthday! Enjoy 20% off as our gift to you.",
                "offer_type": "percentage",
                "discount_value": 20.0,
                "min_purchase_amount": 0,
                "valid_days": 7,
                "reason": f"Birthday {day_str}",
            })

        return suggestions

    except Exception as exc:
        logger.warning("ai_suggest_offers_for_customer failed for #%d: %s", customer_id, exc)
        return []


def get_offer_analytics() -> dict:
    """Return offer performance analytics."""
    total_offers = db.session.execute(db.select(func.count(Offer.id))).scalar() or 0
    total_assigned = db.session.execute(db.select(func.count(CustomerOffer.id))).scalar() or 0
    total_used = db.session.execute(
        db.select(func.count(CustomerOffer.id))
        .where(CustomerOffer.status == CustomerOffer.STATUS_USED)
    ).scalar() or 0
    total_expired = db.session.execute(
        db.select(func.count(CustomerOffer.id))
        .where(CustomerOffer.status == CustomerOffer.STATUS_EXPIRED)
    ).scalar() or 0
    total_unused = db.session.execute(
        db.select(func.count(CustomerOffer.id))
        .where(CustomerOffer.status == CustomerOffer.STATUS_UNUSED)
    ).scalar() or 0

    conversion_rate = round(total_used / total_assigned * 100, 1) if total_assigned else 0.0

    # Per-offer breakdown
    offer_stats = db.session.execute(
        db.select(
            Offer.id,
            Offer.title,
            Offer.offer_type,
            Offer.discount_value,
            Offer.status,
            func.count(CustomerOffer.id).label("assigned"),
            func.sum(
                db.case((CustomerOffer.status == "used", 1), else_=0)
            ).label("used"),
        )
        .outerjoin(CustomerOffer, CustomerOffer.offer_id == Offer.id)
        .group_by(Offer.id, Offer.title, Offer.offer_type, Offer.discount_value, Offer.status)
        .order_by(func.count(CustomerOffer.id).desc())
    ).all()

    return {
        "total_offers": total_offers,
        "total_assigned": total_assigned,
        "total_used": total_used,
        "total_expired": total_expired,
        "total_unused": total_unused,
        "conversion_rate": conversion_rate,
        "offer_stats": [
            {
                "id": row.id,
                "title": row.title,
                "offer_type": row.offer_type,
                "discount_value": float(row.discount_value),
                "status": row.status,
                "assigned": row.assigned or 0,
                "used": int(row.used or 0),
                "conversion_rate": round(int(row.used or 0) / (row.assigned or 1) * 100, 1),
            }
            for row in offer_stats
        ],
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _build_offer_message(co: CustomerOffer, notification_type: str) -> str:
    """Build the SMS/WhatsApp message for an offer notification."""
    customer = co.customer
    offer = co.offer
    name = customer.name if customer else "Valued Customer"

    if notification_type == "assigned":
        return (
            f"Hello {name},\n"
            f"You have received a special offer from SmartMart!\n\n"
            f"🎁 {offer.title}\n"
            f"{offer.description or ''}\n"
            f"Valid till: {co.expiry_date.strftime('%d %b %Y')}\n\n"
            f"Visit us soon to redeem. - SmartMart"
        )
    elif notification_type in ("reminder_2d", "reminder_1d"):
        days = 2 if notification_type == "reminder_2d" else 1
        day_word = "2 days" if days == 2 else "tomorrow"
        return (
            f"Hello {name},\n"
            f"⏰ Your offer expires {day_word}!\n\n"
            f"🎁 {offer.title}\n"
            f"Expires: {co.expiry_date.strftime('%d %b %Y')}\n\n"
            f"Don't miss out — visit SmartMart today!"
        )
    else:
        return (
            f"Hello {name},\n"
            f"Your offer '{offer.title}' has expired.\n"
            f"Visit SmartMart for new offers. - SmartMart"
        )


def _send_offer_assigned_notification(co: CustomerOffer) -> None:
    """Send notification when an offer is assigned to a customer."""
    customer = co.customer
    if not customer or not customer.phone:
        return

    msg = _build_offer_message(co, "assigned")
    channel = "whatsapp" if _whatsapp_enabled() else "sms"

    from .notification_service import send_notification
    log = send_notification(customer.phone, msg, channel=channel)

    notif = OfferNotification(
        customer_offer_id=co.id,
        notification_type="assigned",
        channel=channel,
        status=log.status,
        provider_ref=log.provider_ref,
        error=log.error,
        sent_at=log.sent_at,
    )
    db.session.add(notif)
    db.session.commit()


def _send_offer_reminder_notification(co: CustomerOffer, notif_type: str, days_ahead: int) -> None:
    """Send a reminder notification for an expiring offer."""
    customer = co.customer
    if not customer or not customer.phone:
        return

    msg = _build_offer_message(co, notif_type)
    channel = "whatsapp" if _whatsapp_enabled() else "sms"

    from .notification_service import send_notification
    log = send_notification(customer.phone, msg, channel=channel)

    notif = OfferNotification(
        customer_offer_id=co.id,
        notification_type=notif_type,
        channel=channel,
        status=log.status,
        provider_ref=log.provider_ref,
        error=log.error,
        sent_at=log.sent_at,
    )
    db.session.add(notif)
    db.session.commit()


def _whatsapp_enabled() -> bool:
    import os
    provider = os.environ.get("NOTIFICATION_PROVIDER", "none").lower()
    return provider == "twilio"
