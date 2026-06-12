"""
email_service.py
================
Transactional email service for SmartMart / GoldKernel.

Requires Flask-Mail configured via environment variables:
  MAIL_SERVER, MAIL_PORT, MAIL_USE_TLS, MAIL_USERNAME,
  MAIL_PASSWORD, MAIL_DEFAULT_SENDER

If MAIL_SERVER is not set, all emails are logged only (no-op mode).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _mail_configured() -> bool:
    return bool(os.environ.get("MAIL_SERVER", ""))


def _get_mail():
    """Return the Flask-Mail instance from current app extensions."""
    try:
        from flask import current_app
        return current_app.extensions.get("mail")
    except Exception:
        return None


def send_order_confirmation(order, items: list) -> bool:
    """
    Send order confirmation email to the customer.
    Returns True if sent (or logged), False on error.

    order: OnlineOrder instance
    items: list of dicts with keys: name, qty, unit_price, subtotal
    """
    customer_email = getattr(order, "customer_email", None)
    if not customer_email:
        logger.info(
            "No customer email for order %s — skipping confirmation email",
            order.order_number
        )
        return False

    if not _mail_configured():
        logger.info(
            "[EMAIL NO-OP] Order confirmation for %s to %s (MAIL_SERVER not configured)",
            order.order_number, customer_email
        )
        return True

    mail = _get_mail()
    if not mail:
        logger.warning("Flask-Mail not initialised — cannot send order confirmation")
        return False

    try:
        from flask_mail import Message
        subject = f"Order Confirmed — {order.order_number} | GoldKernel Dry Fruits"
        html_body = _order_confirmation_html(order, items)
        text_body = _order_confirmation_text(order, items)
        msg = Message(
            subject=subject,
            recipients=[customer_email],
            html=html_body,
            body=text_body,
        )
        mail.send(msg)
        logger.info("Order confirmation email sent: %s → %s", order.order_number, customer_email)
        return True
    except Exception as exc:
        logger.error(
            "Failed to send order confirmation for %s: %s",
            order.order_number, exc
        )
        return False


def send_admin_new_order_notification(order, admin_email: Optional[str]) -> bool:
    """
    Send new order notification email to the admin.
    Falls back to SMS notification if no email configured.
    """
    if not admin_email:
        return False
    if not _mail_configured():
        logger.info(
            "[EMAIL NO-OP] Admin order notification for %s (MAIL_SERVER not configured)",
            order.order_number
        )
        return True

    mail = _get_mail()
    if not mail:
        return False

    try:
        from flask_mail import Message
        subject = f"🛒 New Order: {order.order_number} — NPR {order.grand_total:.0f}"
        html_body = _admin_order_html(order)
        msg = Message(
            subject=subject,
            recipients=[admin_email],
            html=html_body,
        )
        mail.send(msg)
        logger.info("Admin notification sent for order %s", order.order_number)
        return True
    except Exception as exc:
        logger.error("Failed to send admin notification: %s", exc)
        return False


def send_password_reset_email(user, reset_url: str) -> bool:
    """Send password reset link via email if configured."""
    user_email = getattr(user, "email", None)
    if not user_email:
        return False
    if not _mail_configured():
        logger.info("[EMAIL NO-OP] Password reset for %s (MAIL_SERVER not configured)", user.username)
        return True

    mail = _get_mail()
    if not mail:
        return False

    try:
        from flask_mail import Message
        msg = Message(
            subject="Reset Your SmartMart Password",
            recipients=[user_email],
            html=f"""
            <p>Hello {user.username},</p>
            <p>Click the link below to reset your password (valid for 30 minutes):</p>
            <p><a href="{reset_url}">{reset_url}</a></p>
            <p>If you did not request this, please ignore this email.</p>
            <p>— SmartMart Team</p>
            """,
        )
        mail.send(msg)
        return True
    except Exception as exc:
        logger.error("Failed to send password reset email: %s", exc)
        return False


# ── HTML templates ────────────────────────────────────────────────────────────

def _order_confirmation_html(order, items: list) -> str:
    items_rows = "".join(
        f"""<tr>
          <td style="padding:8px;border-bottom:1px solid #eee">{it.get('name','')}</td>
          <td style="padding:8px;border-bottom:1px solid #eee;text-align:center">{it.get('qty','')}</td>
          <td style="padding:8px;border-bottom:1px solid #eee;text-align:right">NPR {float(it.get('unit_price',0)):,.2f}</td>
          <td style="padding:8px;border-bottom:1px solid #eee;text-align:right">NPR {float(it.get('subtotal',0)):,.2f}</td>
        </tr>"""
        for it in items
    )
    payment_badge = {
        "cod": "💵 Cash on Delivery",
        "esewa": "📱 eSewa",
        "khalti": "💜 Khalti",
    }.get(str(getattr(order, "payment_mode", "cod")).lower(), "Online Payment")

    return f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;color:#333">
      <div style="background:#f8b400;padding:20px;text-align:center;border-radius:8px 8px 0 0">
        <h1 style="color:#fff;margin:0">🌰 GoldKernel Dry Fruits</h1>
        <p style="color:#fff;margin:4px 0">Order Confirmed!</p>
      </div>
      <div style="background:#fff;padding:24px;border:1px solid #eee;border-top:none;border-radius:0 0 8px 8px">
        <h2 style="color:#f8b400">Order #{order.order_number}</h2>
        <p>Dear <strong>{order.customer_name}</strong>,</p>
        <p>Thank you for your order! We've received it and will process it shortly.</p>

        <table style="width:100%;border-collapse:collapse;margin:16px 0">
          <thead>
            <tr style="background:#f5f5f5">
              <th style="padding:8px;text-align:left">Product</th>
              <th style="padding:8px;text-align:center">Qty</th>
              <th style="padding:8px;text-align:right">Unit Price</th>
              <th style="padding:8px;text-align:right">Total</th>
            </tr>
          </thead>
          <tbody>{items_rows}</tbody>
        </table>

        <table style="width:100%;margin:8px 0">
          <tr><td>Subtotal</td><td style="text-align:right">NPR {float(getattr(order,'total_amount',0)):,.2f}</td></tr>
          <tr><td>Delivery</td><td style="text-align:right">NPR {float(getattr(order,'delivery_charge',0)):,.2f}</td></tr>
          <tr style="font-weight:bold;font-size:16px">
            <td>Grand Total</td>
            <td style="text-align:right;color:#f8b400">NPR {float(order.grand_total):,.2f}</td>
          </tr>
        </table>

        <p><strong>Payment:</strong> {payment_badge}</p>
        <p><strong>Delivery Address:</strong> {getattr(order,'delivery_address','')}, {getattr(order,'delivery_area','')}</p>

        <div style="background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:12px;margin:16px 0">
          <p style="margin:0">📦 Track your order at <strong>goldkernel.com/store/track</strong> using order number <strong>{order.order_number}</strong></p>
        </div>

        <p style="color:#666;font-size:12px">Questions? WhatsApp or call us. Thank you for choosing GoldKernel! 🌟</p>
      </div>
    </body>
    </html>
    """


def _order_confirmation_text(order, items: list) -> str:
    items_text = "\n".join(
        f"  • {it.get('name','')} × {it.get('qty','')} = NPR {float(it.get('subtotal',0)):,.2f}"
        for it in items
    )
    return f"""
GoldKernel Dry Fruits — Order Confirmed!

Order: {order.order_number}
Customer: {order.customer_name}

Items:
{items_text}

Grand Total: NPR {float(order.grand_total):,.2f}
Delivery to: {getattr(order,'delivery_address','')}, {getattr(order,'delivery_area','')}

Track at: goldkernel.com/store/track (use order number: {order.order_number})

Thank you for choosing GoldKernel!
"""


def _admin_order_html(order) -> str:
    return f"""
    <p><strong>New Online Order Received</strong></p>
    <table style="border-collapse:collapse;font-family:Arial,sans-serif">
      <tr><td style="padding:4px 12px"><strong>Order #</strong></td><td>{order.order_number}</td></tr>
      <tr><td style="padding:4px 12px"><strong>Customer</strong></td><td>{order.customer_name}</td></tr>
      <tr><td style="padding:4px 12px"><strong>Phone</strong></td><td>{order.customer_phone}</td></tr>
      <tr><td style="padding:4px 12px"><strong>Amount</strong></td><td>NPR {float(order.grand_total):,.2f}</td></tr>
      <tr><td style="padding:4px 12px"><strong>Payment</strong></td><td>{getattr(order,'payment_mode','')}</td></tr>
      <tr><td style="padding:4px 12px"><strong>Delivery</strong></td><td>{getattr(order,'delivery_address','')}, {getattr(order,'delivery_area','')}</td></tr>
    </table>
    <p><a href="/admin/ecommerce/orders/{order.id}">View Order in Admin →</a></p>
    """
