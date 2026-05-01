"""Offer cron jobs — run via scheduler or manual trigger.

Can be called from:
  1. The /offers/api/run-cron endpoint (admin manual trigger)
  2. A cron job: python -c "from smart_mart.services.offer_cron import run_all; run_all()"
  3. APScheduler (if configured)

Jobs:
  - expire_stale_offers: Mark past-expiry unused offers as expired
  - send_expiry_reminders: Send 2d and 1d before expiry reminders
  - retry_failed_notifications: Retry failed SMS/WhatsApp sends
  - ai_auto_assign_offers: Auto-generate and assign AI offers to at-risk customers
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def run_all(app=None) -> dict:
    """Run all offer cron jobs. Pass app for standalone execution."""
    if app:
        with app.app_context():
            return _run_jobs()
    return _run_jobs()


def _run_jobs() -> dict:
    from .offer_service import (
        expire_stale_offers,
        send_expiry_reminders,
        retry_failed_notifications,
    )

    results = {}

    # 1. Expire stale offers
    try:
        results["expired"] = expire_stale_offers()
        logger.info("Cron: expired %d stale offers", results["expired"])
    except Exception as e:
        logger.error("Cron: expire_stale_offers failed: %s", e)
        results["expired"] = 0

    # 2. Send expiry reminders
    try:
        results["reminders"] = send_expiry_reminders()
        logger.info("Cron: sent reminders: %s", results["reminders"])
    except Exception as e:
        logger.error("Cron: send_expiry_reminders failed: %s", e)
        results["reminders"] = {"2d": 0, "1d": 0, "failed": 0}

    # 3. Retry failed notifications
    try:
        results["retried"] = retry_failed_notifications()
        logger.info("Cron: retried %d failed notifications", results["retried"])
    except Exception as e:
        logger.error("Cron: retry_failed_notifications failed: %s", e)
        results["retried"] = 0

    # 4. AI auto-assign offers to at-risk customers
    try:
        results["ai_assigned"] = _ai_auto_assign()
        logger.info("Cron: AI auto-assigned %d offers", results["ai_assigned"])
    except Exception as e:
        logger.error("Cron: ai_auto_assign failed: %s", e)
        results["ai_assigned"] = 0

    results["ran_at"] = datetime.now(timezone.utc).isoformat()
    return results


def _ai_auto_assign() -> int:
    """Auto-generate and assign AI offers to customers who need retention nudges.

    Targets:
      - Customers with no visit in 14+ days (comeback offer)
      - Customers with birthday in next 7 days (birthday offer)

    Uses shared template offers to avoid creating thousands of duplicate Offer rows.
    """
    from ..extensions import db
    from ..models.customer import Customer
    from ..models.offer import Offer, CustomerOffer
    from ..models.user import User
    from .offer_service import ai_suggest_offers_for_customer, assign_offer_to_customer
    from datetime import date, timedelta

    today = date.today()
    assigned_count = 0

    # Find or create system-level template offers (one per type, reused for all customers)
    # Use the first admin user as creator, or skip if none exists
    admin_user = db.session.execute(
        db.select(User).where(User.role == "admin", User.is_active == True)
        .order_by(User.id)
    ).scalar_one_or_none()
    if not admin_user:
        logger.warning("AI auto-assign: no active admin user found — skipping")
        return 0

    def _get_or_create_template_offer(title: str, offer_type: str, discount_value: float,
                                       min_purchase: float, valid_days: int) -> Offer:
        """Reuse existing template offer or create one."""
        existing = db.session.execute(
            db.select(Offer).where(
                Offer.title == title,
                Offer.offer_type == offer_type,
                Offer.status == "active",
            )
        ).scalar_one_or_none()
        if existing:
            return existing
        offer = Offer(
            title=title,
            offer_type=offer_type,
            discount_value=discount_value,
            min_purchase_amount=min_purchase if min_purchase > 0 else None,
            valid_days=valid_days,
            usage_limit=1,
            created_by=admin_user.id,
            status="active",
        )
        db.session.add(offer)
        db.session.flush()
        return offer

    # Get customers with phone numbers (can receive notifications)
    # Process in batches of 100 to avoid memory issues
    batch_size = 100
    offset = 0

    while True:
        customers = db.session.execute(
            db.select(Customer)
            .where(Customer.phone.isnot(None), Customer.phone != "")
            .order_by(Customer.id)
            .limit(batch_size)
            .offset(offset)
        ).scalars().all()

        if not customers:
            break

        for customer in customers:
            try:
                suggestions = ai_suggest_offers_for_customer(customer.id)
                if not suggestions:
                    continue

                # Only auto-assign comeback and birthday offers
                priority_types = {"comeback", "birthday"}
                for suggestion in suggestions:
                    if suggestion["type"] not in priority_types:
                        continue

                    # Check if customer already has ANY active offer of this type
                    existing_co = db.session.execute(
                        db.select(CustomerOffer)
                        .join(Offer, Offer.id == CustomerOffer.offer_id)
                        .where(
                            CustomerOffer.customer_id == customer.id,
                            CustomerOffer.status == CustomerOffer.STATUS_UNUSED,
                            CustomerOffer.expiry_date >= today,
                            Offer.offer_type == suggestion["offer_type"],
                        )
                    ).scalar_one_or_none()

                    if existing_co:
                        continue  # Already has a similar active offer

                    # Get or create template offer (reused across customers)
                    template = _get_or_create_template_offer(
                        title=suggestion["title"],
                        offer_type=suggestion["offer_type"],
                        discount_value=suggestion["discount_value"],
                        min_purchase=suggestion.get("min_purchase_amount", 0),
                        valid_days=suggestion["valid_days"],
                    )

                    assign_offer_to_customer(
                        customer_id=customer.id,
                        offer_id=template.id,
                        send_notification=True,
                    )
                    assigned_count += 1

            except Exception as e:
                logger.warning("AI auto-assign failed for customer #%d: %s", customer.id, e)
                db.session.rollback()
                continue

        offset += batch_size
        if len(customers) < batch_size:
            break

    return assigned_count


if __name__ == "__main__":
    """Standalone execution: python -m smart_mart.services.offer_cron"""
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from smart_mart.app import create_app
    app = create_app(os.environ.get("FLASK_ENV", "development"))
    results = run_all(app)
    print("Cron results:", results)
