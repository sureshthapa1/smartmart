"""Notification service — SMS/WhatsApp stubs with logging.

Providers can be configured via environment variables:
  NOTIFICATION_PROVIDER = sparrow | twilio | none (default)
  SPARROW_TOKEN = <token>
  TWILIO_SID / TWILIO_TOKEN / TWILIO_FROM = <credentials>
"""
from __future__ import annotations
import os
import logging
from datetime import datetime, timezone
from ..extensions import db
from ..models.notification_log import NotificationLog

logger = logging.getLogger(__name__)


def _get_provider() -> str:
    return os.environ.get("NOTIFICATION_PROVIDER", "none").lower()


def _send_sparrow(phone: str, message: str) -> tuple[bool, str]:
    """Sparrow SMS (Nepal) integration."""
    try:
        import urllib.request, urllib.parse, json
        token = os.environ.get("SPARROW_TOKEN", "")
        if not token:
            return False, "SPARROW_TOKEN not configured"
        data = urllib.parse.urlencode({
            "token": token, "from": "SmartMart",
            "to": phone, "text": message
        }).encode()
        req = urllib.request.Request("http://api.sparrowsms.com/v2/sms/", data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("response_code") == 200:
                return True, str(result.get("uid", ""))
            return False, str(result)
    except Exception as e:
        return False, str(e)


def _send_twilio(phone: str, message: str, whatsapp: bool = False) -> tuple[bool, str]:
    try:
        from twilio.rest import Client
        sid = os.environ.get("TWILIO_SID", "")
        token = os.environ.get("TWILIO_TOKEN", "")
        from_num = os.environ.get("TWILIO_FROM", "")
        if not all([sid, token, from_num]):
            return False, "Twilio credentials not configured"
        client = Client(sid, token)
        prefix = "whatsapp:" if whatsapp else ""
        msg = client.messages.create(
            body=message,
            from_=f"{prefix}{from_num}",
            to=f"{prefix}{phone}"
        )
        return True, msg.sid
    except Exception as e:
        return False, str(e)


def send_notification(phone: str, message: str, channel: str = "sms") -> NotificationLog:
    """Send a notification and log it. Falls back to log-only if no provider configured."""
    log = NotificationLog(recipient=phone, channel=channel, message=message)
    db.session.add(log)
    db.session.flush()

    provider = _get_provider()
    success, ref = False, "no_provider"

    if provider == "sparrow" and channel == "sms":
        success, ref = _send_sparrow(phone, message)
    elif provider == "twilio":
        success, ref = _send_twilio(phone, message, whatsapp=(channel == "whatsapp"))
    else:
        # Log-only mode — useful for development
        logger.info(f"[NOTIFICATION] {channel.upper()} to {phone}: {message}")
        success, ref = True, "logged_only"

    log.status = "sent" if success else "failed"
    log.provider_ref = ref if success else None
    log.error = ref if not success else None
    log.sent_at = datetime.now(timezone.utc) if success else None
    db.session.commit()
    return log


# ── Convenience helpers ───────────────────────────────────────────────────────

def notify_credit_overdue(customer_name: str, phone: str, amount: float, sale_id: int):
    msg = (f"Dear {customer_name}, your credit payment of NPR {amount:,.2f} "
           f"(Sale #{sale_id}) at Smart Mart is overdue. Please clear at your earliest. Thank you.")
    return send_notification(phone, msg)


def notify_order_status(customer_name: str, phone: str, order_number: str, status: str):
    status_msgs = {
        "confirmed": f"Your order {order_number} has been confirmed.",
        "preparing": f"Your order {order_number} is being prepared.",
        "out_for_delivery": f"Your order {order_number} is out for delivery!",
        "delivered": f"Your order {order_number} has been delivered. Thank you!",
        "cancelled": f"Your order {order_number} has been cancelled.",
    }
    msg = f"Dear {customer_name}, {status_msgs.get(status, f'Order {order_number} status: {status}.')} - Smart Mart"
    return send_notification(phone, msg)


def notify_low_stock(product_name: str, quantity: int, admin_phone: str):
    msg = f"[Smart Mart Alert] Low stock: {product_name} has only {quantity} units left. Restock needed."
    return send_notification(admin_phone, msg)
