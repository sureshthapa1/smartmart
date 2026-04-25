"""bot_runner — scheduled daily bot tasks.

Call POST /api/bot/run (with BOT_SECRET header) from a cron job or
Render's scheduler to run all daily automation tasks.

Tasks:
  1. low_stock_bot        — notifications + SMS for products at/below reorder point
  2. credit_bot           — overdue credit reminders + SMS to customers
  3. reorder_bot          — auto-creates draft POs for critical stock
  4. expense_bot          — recurring bill reminders
  5. expiry_bot           — expiry date warnings
  6. daily_summary_bot    — NLG daily summary
  7. risk_score_bot       — refreshes customer credit risk scores
  8. anomaly_bot          — flags suspicious discounts and price variance
  9. pending_orders_bot   — alerts on online orders stuck in pending/preparing
  10. promotion_bot       — alerts on promotions expiring today or tomorrow
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from ..extensions import db

logger = logging.getLogger(__name__)


# ── 1. Low Stock Bot ──────────────────────────────────────────────────────────

def run_low_stock_bot() -> dict:
    """Create AppNotification for every product at or below its reorder point."""
    from ..models.product import Product
    from ..models.operations import AppNotification

    products = db.session.execute(
        db.select(Product)
        .where(Product.quantity <= Product.reorder_point)
        .where(Product.quantity >= 0)
        .order_by(Product.quantity.asc())
    ).scalars().all()

    created = 0
    for p in products:
        urgency = "critical" if p.quantity == 0 else "warning"
        msg = (
            f"OUT OF STOCK: {p.name} (SKU: {p.sku})" if p.quantity == 0
            else f"Low stock: {p.name} — {p.quantity} {p.unit or 'pcs'} left (reorder at {p.reorder_point})"
        )
        # Avoid duplicate notifications for same product on same day
        existing = db.session.execute(
            db.select(AppNotification).where(
                AppNotification.message.like(f"%{p.name}%"),
                AppNotification.created_at >= datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ),
            )
        ).scalars().first()
        if existing:
            continue
        db.session.add(AppNotification(
            notification_type=urgency,
            message=msg,
            entity_type="Product",
            entity_id=p.id,
        ))
        created += 1

    if created:
        db.session.commit()
    logger.info("low_stock_bot: %d notifications created", created)

    # SMS admin if provider configured
    try:
        from ..models.shop_settings import ShopSettings
        from .notification_service import notify_low_stock
        admin_phone = ShopSettings.get().phone
        if admin_phone and created > 0:
            for p in products[:3]:  # cap at 3 SMS to avoid spam
                notify_low_stock(p.name, p.quantity, admin_phone)
    except Exception as e:
        logger.warning("low_stock_bot SMS failed: %s", e)

    return {"task": "low_stock_bot", "notifications_created": created, "products_affected": len(products)}


# ── 2. Credit Collection Bot ──────────────────────────────────────────────────

def run_credit_bot() -> dict:
    """Flag overdue credit sales and create collection reminder notifications."""
    from ..models.sale import Sale
    from ..models.operations import AppNotification

    today = date.today()
    overdue = db.session.execute(
        db.select(Sale).where(
            Sale.payment_mode == "credit",
            Sale.credit_collected == False,
            Sale.credit_due_date < today,
        ).order_by(Sale.credit_due_date.asc())
    ).scalars().all()

    created = 0
    for sale in overdue:
        days_overdue = (today - sale.credit_due_date).days
        msg = (
            f"Overdue credit: {sale.customer_name or 'Customer'} — "
            f"NPR {float(sale.total_amount):,.0f} "
            f"({days_overdue} day{'s' if days_overdue != 1 else ''} overdue) "
            f"Invoice: {sale.invoice_number or sale.id}"
        )
        existing = db.session.execute(
            db.select(AppNotification).where(
                AppNotification.entity_type == "Sale",
                AppNotification.entity_id == sale.id,
                AppNotification.notification_type == "credit_overdue",
                AppNotification.created_at >= datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ),
            )
        ).scalars().first()
        if existing:
            continue
        db.session.add(AppNotification(
            notification_type="credit_overdue",
            message=msg,
            entity_type="Sale",
            entity_id=sale.id,
        ))
        created += 1

    if created:
        db.session.commit()
    logger.info("credit_bot: %d overdue reminders created", created)

    # SMS customers with phone numbers if provider configured
    try:
        from .notification_service import notify_credit_overdue
        for sale in overdue[:10]:  # cap at 10 SMS per run
            if sale.customer_phone:
                notify_credit_overdue(
                    customer_name=sale.customer_name or "Customer",
                    phone=sale.customer_phone,
                    amount=float(sale.total_amount),
                    sale_id=sale.id,
                )
    except Exception as e:
        logger.warning("credit_bot SMS failed: %s", e)

    return {"task": "credit_bot", "overdue_count": len(overdue), "notifications_created": created}


# ── 3. Reorder Bot ────────────────────────────────────────────────────────────

def run_reorder_bot(user_id: int = 1) -> dict:
    """Auto-create draft POs for critical-urgency products (out of stock or < 1 day left)."""
    try:
        from .ai_growth_ops import auto_replenishment_plan
        from ..models.purchase_order import PurchaseOrder
        from . import po_manager

        plan = auto_replenishment_plan(lookback_days=30, safety_days=3, coverage_days=14)
        pos_created = []

        for grp in plan["supplier_groups"]:
            critical_items = [i for i in grp["items"] if i["urgency"] == "critical"]
            if not critical_items:
                continue

            # Check if a draft PO for this supplier was already created today
            existing_po = db.session.execute(
                db.select(PurchaseOrder).where(
                    PurchaseOrder.supplier_id == grp["supplier_id"],
                    PurchaseOrder.status == "draft",
                    db.func.date(PurchaseOrder.created_at) == date.today(),
                )
            ).scalars().first()
            if existing_po:
                continue

            try:
                po = po_manager.create_po(
                    supplier_id=grp["supplier_id"],
                    items=[{
                        "product_id": i["product_id"],
                        "quantity": i["recommended_qty"],
                        "unit_cost": i["unit_cost"],
                    } for i in critical_items],
                    user_id=user_id,
                    notes="Auto-created by Reorder Bot — critical stock level",
                )
                pos_created.append({"po_id": po.id, "supplier": grp["supplier_name"],
                                     "items": len(critical_items)})
            except Exception as e:
                logger.warning("reorder_bot: PO creation failed for supplier %s: %s",
                               grp["supplier_name"], e)

        logger.info("reorder_bot: %d POs created", len(pos_created))
        return {"task": "reorder_bot", "pos_created": len(pos_created), "details": pos_created}
    except Exception as e:
        logger.warning("reorder_bot failed: %s", e)
        return {"task": "reorder_bot", "pos_created": 0, "error": str(e)}


# ── 4. Recurring Expense Bot ──────────────────────────────────────────────────

def run_expense_bot() -> dict:
    """Create notifications for recurring bills due within their reminder window."""
    from ..models.recurring_expense import RecurringExpense
    from ..models.operations import AppNotification

    today = date.today()
    items = db.session.execute(
        db.select(RecurringExpense).where(RecurringExpense.is_active == True)
    ).scalars().all()

    created = 0
    for item in items:
        days_until = (item.next_due_date - today).days
        if days_until > item.reminder_days:
            continue

        overdue = days_until < 0
        msg = (
            f"OVERDUE bill: {item.name} — NPR {float(item.amount):,.0f} "
            f"(was due {abs(days_until)} day{'s' if abs(days_until) != 1 else ''} ago)"
            if overdue else
            f"Bill due {'today' if days_until == 0 else f'in {days_until} day(s)'}: "
            f"{item.name} — NPR {float(item.amount):,.0f}"
        )
        existing = db.session.execute(
            db.select(AppNotification).where(
                AppNotification.entity_type == "RecurringExpense",
                AppNotification.entity_id == item.id,
                AppNotification.created_at >= datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ),
            )
        ).scalars().first()
        if existing:
            continue
        db.session.add(AppNotification(
            notification_type="expense_due" if not overdue else "expense_overdue",
            message=msg,
            entity_type="RecurringExpense",
            entity_id=item.id,
        ))
        created += 1

    if created:
        db.session.commit()
    logger.info("expense_bot: %d bill reminders created", created)
    return {"task": "expense_bot", "bills_checked": len(items), "notifications_created": created}


# ── 5. Expiry Bot ────────────────────────────────────────────────────────────

def run_expiry_bot() -> dict:
    """Create notifications for products expiring within their warning window."""
    from ..models.operations import AppNotification
    from .alert_engine import get_expiry_alerts

    expiring = get_expiry_alerts()
    created = 0
    for p in expiring:
        days_left = (p.expiry_date - date.today()).days
        msg = (
            f"EXPIRED: {p.name} (SKU: {p.sku}) — expired {abs(days_left)} day(s) ago"
            if days_left < 0 else
            f"Expiring {'today' if days_left == 0 else f'in {days_left} day(s)'}: "
            f"{p.name} (SKU: {p.sku}) — {p.quantity} {p.unit or 'pcs'} in stock"
        )
        existing = db.session.execute(
            db.select(AppNotification).where(
                AppNotification.entity_type == "Product",
                AppNotification.entity_id == p.id,
                AppNotification.notification_type.in_(["expiry_warning", "expired"]),
                AppNotification.created_at >= datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ),
            )
        ).scalars().first()
        if existing:
            continue
        db.session.add(AppNotification(
            notification_type="expired" if days_left < 0 else "expiry_warning",
            message=msg,
            entity_type="Product",
            entity_id=p.id,
        ))
        created += 1

    if created:
        db.session.commit()
    logger.info("expiry_bot: %d expiry notifications created", created)
    return {"task": "expiry_bot", "products_checked": len(expiring), "notifications_created": created}


# ── 6. Daily Summary Bot ──────────────────────────────────────────────────────

def run_daily_summary_bot() -> dict:
    """Generate and store today's NLG daily summary as a notification."""
    from ..models.operations import AppNotification
    try:
        from .ai_nlg import generate_daily_report
        report = generate_daily_report()
        summary = report.get("narrative", "")
        if not summary:
            return {"task": "daily_summary_bot", "status": "no_data"}

        # Store as a notification so it persists
        db.session.add(AppNotification(
            notification_type="daily_summary",
            message=summary[:1000],  # truncate for storage
            entity_type="System",
            entity_id=0,
        ))
        db.session.commit()
        logger.info("daily_summary_bot: summary stored")
        return {"task": "daily_summary_bot", "status": "ok", "data": report["data"]}
    except Exception as e:
        logger.warning("daily_summary_bot failed: %s", e)
        return {"task": "daily_summary_bot", "status": "error", "error": str(e)}


# ── 7. Risk Score Bot ────────────────────────────────────────────────────────

def run_risk_score_bot() -> dict:
    """Refresh credit risk scores for all customers who have credit sales."""
    try:
        from .credit_risk_service import recalculate_all
        count = recalculate_all()
        logger.info("risk_score_bot: %d customer scores refreshed", count)
        return {"task": "risk_score_bot", "customers_updated": count}
    except Exception as e:
        logger.warning("risk_score_bot failed: %s", e)
        return {"task": "risk_score_bot", "customers_updated": 0, "error": str(e)}


# ── 8. Anomaly Bot ────────────────────────────────────────────────────────────

def run_anomaly_bot() -> dict:
    """Detect suspicious discounts and price anomalies. Alert if high severity found."""
    from ..models.operations import AppNotification
    try:
        from .ai_anomaly_detection import detect_suspicious_discounts, detect_price_anomalies

        discounts = detect_suspicious_discounts(days=7)
        prices = detect_price_anomalies(days=7)

        created = 0
        for item in discounts:
            if item["severity"] == "high":
                msg = f"⚠️ Suspicious discounts by {item['username']}: avg {item['avg_discount_pct']}% discount on {item['discount_count']} sales (last 7 days)"
                existing = db.session.execute(
                    db.select(AppNotification).where(
                        AppNotification.notification_type == "anomaly_discount",
                        AppNotification.entity_id == item["user_id"],
                        AppNotification.created_at >= datetime.now(timezone.utc).replace(
                            hour=0, minute=0, second=0, microsecond=0
                        ),
                    )
                ).scalars().first()
                if not existing:
                    db.session.add(AppNotification(
                        notification_type="anomaly_discount",
                        message=msg,
                        entity_type="User",
                        entity_id=item["user_id"],
                    ))
                    created += 1

        for item in prices:
            if item["severity"] == "high":
                msg = f"⚠️ Price anomaly: {item['product_name']} sold at NPR {item['min_price']:.0f}–{item['max_price']:.0f} ({item['variance_pct']}% variance, last 7 days)"
                existing = db.session.execute(
                    db.select(AppNotification).where(
                        AppNotification.notification_type == "anomaly_price",
                        AppNotification.entity_id == item["product_id"],
                        AppNotification.created_at >= datetime.now(timezone.utc).replace(
                            hour=0, minute=0, second=0, microsecond=0
                        ),
                    )
                ).scalars().first()
                if not existing:
                    db.session.add(AppNotification(
                        notification_type="anomaly_price",
                        message=msg,
                        entity_type="Product",
                        entity_id=item["product_id"],
                    ))
                    created += 1

        if created:
            db.session.commit()
        logger.info("anomaly_bot: %d anomaly alerts created", created)
        return {
            "task": "anomaly_bot",
            "suspicious_discounts": len(discounts),
            "price_anomalies": len(prices),
            "notifications_created": created,
        }
    except Exception as e:
        logger.warning("anomaly_bot failed: %s", e)
        return {"task": "anomaly_bot", "notifications_created": 0, "error": str(e)}


# ── 9. Pending Orders Bot ─────────────────────────────────────────────────────

def run_pending_orders_bot(stale_hours: int = 2) -> dict:
    """Alert on online orders stuck in pending or preparing for too long."""
    from ..models.online_order import OnlineOrder
    from ..models.operations import AppNotification

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - __import__('datetime').timedelta(hours=stale_hours)

    stale = db.session.execute(
        db.select(OnlineOrder).where(
            OnlineOrder.status.in_(["pending", "confirmed", "preparing"]),
            OnlineOrder.created_at <= cutoff,
        ).order_by(OnlineOrder.created_at.asc())
    ).scalars().all()

    created = 0
    for order in stale:
        age_hours = round((datetime.now(timezone.utc).replace(tzinfo=None) - order.created_at).total_seconds() / 3600, 1)
        msg = (
            f"⏰ Order {order.order_number} stuck in '{order.status}' for {age_hours}h — "
            f"{order.customer_name or 'Customer'}"
        )
        existing = db.session.execute(
            db.select(AppNotification).where(
                AppNotification.notification_type == "order_stale",
                AppNotification.entity_id == order.id,
                AppNotification.created_at >= datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ),
            )
        ).scalars().first()
        if existing:
            continue
        db.session.add(AppNotification(
            notification_type="order_stale",
            message=msg,
            entity_type="OnlineOrder",
            entity_id=order.id,
        ))
        created += 1

    if created:
        db.session.commit()
    logger.info("pending_orders_bot: %d stale order alerts", created)
    return {"task": "pending_orders_bot", "stale_orders": len(stale), "notifications_created": created}


# ── 10. Promotion Expiry Bot ──────────────────────────────────────────────────

def run_promotion_bot() -> dict:
    """Alert on promotions expiring today or tomorrow."""
    from ..models.promotion import Promotion
    from ..models.operations import AppNotification

    today = date.today()
    tomorrow = today + __import__('datetime').timedelta(days=1)

    expiring = db.session.execute(
        db.select(Promotion).where(
            Promotion.is_active == True,
            Promotion.end_date.in_([today, tomorrow]),
        )
    ).scalars().all()

    created = 0
    for promo in expiring:
        days_left = (promo.end_date - today).days
        msg = (
            f"🏷️ Promotion '{promo.name}' expires {'today' if days_left == 0 else 'tomorrow'} "
            f"({promo.end_date})"
        )
        existing = db.session.execute(
            db.select(AppNotification).where(
                AppNotification.notification_type == "promotion_expiring",
                AppNotification.entity_id == promo.id,
                AppNotification.created_at >= datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ),
            )
        ).scalars().first()
        if existing:
            continue
        db.session.add(AppNotification(
            notification_type="promotion_expiring",
            message=msg,
            entity_type="Promotion",
            entity_id=promo.id,
        ))
        created += 1

    if created:
        db.session.commit()
    logger.info("promotion_bot: %d expiry alerts", created)
    return {"task": "promotion_bot", "expiring_count": len(expiring), "notifications_created": created}


# ── Master runner ─────────────────────────────────────────────────────────────

def run_all_bots(user_id: int = 1) -> dict:
    """Run all daily bots. Returns a summary of what each bot did."""
    results = {}
    for name, fn in [
        ("low_stock_bot",      run_low_stock_bot),
        ("credit_bot",         run_credit_bot),
        ("expiry_bot",         run_expiry_bot),
        ("expense_bot",        run_expense_bot),
        ("daily_summary_bot",  run_daily_summary_bot),
        ("risk_score_bot",     run_risk_score_bot),
        ("anomaly_bot",        run_anomaly_bot),
        ("pending_orders_bot", run_pending_orders_bot),
        ("promotion_bot",      run_promotion_bot),
    ]:
        try:
            results[name] = fn()
        except Exception as e:
            logger.exception("Bot %s failed: %s", name, e)
            results[name] = {"task": name, "error": str(e)}

    # Reorder bot needs user_id
    try:
        results["reorder_bot"] = run_reorder_bot(user_id=user_id)
    except Exception as e:
        results["reorder_bot"] = {"task": "reorder_bot", "error": str(e)}

    return {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "total_notifications": sum(
            r.get("notifications_created", 0) for r in results.values()
        ),
    }
