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


def _ai_personalised_email_section(items: list) -> str:
    """Generate personalised product tips + upsell using Gemini."""
    from .gemini_client import gemini_generate, gemini_available
    if not gemini_available() or not items:
        return ""
    try:
        product_names = ", ".join(i.get("name", "") for i in items[:4])
        prompt = (
            f"A customer in Nepal just ordered: {product_names}\n\n"
            f"Write 2-3 short sentences (no bullets) for an order confirmation email:\n"
            f"1. A specific health/nutrition tip for one of their products\n"
            f"2. A serving suggestion or recipe idea\n"
            f"3. (Optional) One complementary product they might enjoy next time\n\n"
            f"Keep it warm, helpful, and specific to dry fruits. No generic text."
        )
        return gemini_generate(prompt, max_tokens=160) or ""
    except Exception:
        return ""


def send_order_confirmation(order, items: list) -> bool:
    """Send personalised order confirmation email — Gemini generates product tips."""
    customer_email = getattr(order, "customer_email", None)
    if not customer_email:
        logger.info("No customer email for order %s", order.order_number)
        return False
    if not _mail_configured():
        logger.info("[EMAIL NO-OP] Order confirmation for %s (MAIL_SERVER not configured)", order.order_number)
        return True
    mail = _get_mail()
    if not mail:
        logger.warning("Flask-Mail not initialised")
        return False
    try:
        from flask_mail import Message
        # Generate AI personalisation (non-blocking — empty string if fails)
        personalised_tips = _ai_personalised_email_section(items)
        subject = f"Order Confirmed — {order.order_number} | GoldKernel Dry Fruits"
        html_body = _order_confirmation_html(order, items, personalised_tips)
        text_body = _order_confirmation_text(order, items)
        msg = Message(subject=subject, recipients=[customer_email],
                      html=html_body, body=text_body)
        mail.send(msg)
        logger.info("Order confirmation email sent: %s → %s", order.order_number, customer_email)
        return True
    except Exception as exc:
        logger.error("Failed to send order confirmation for %s: %s", order.order_number, exc)
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
        html_body = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
        <body style="margin:0;padding:0;background:#fffbf5;font-family:'Helvetica Neue',Arial,sans-serif;">
          <table width="100%" cellpadding="0" cellspacing="0" style="background:#fffbf5;padding:32px 16px;">
            <tr><td align="center">
              <table width="520" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:16px;border:1px solid #e8e0d4;overflow:hidden;">
                <!-- Header -->
                <tr><td style="background:linear-gradient(135deg,#92400e,#b45309);padding:28px 32px;text-align:center;">
                  <div style="font-size:28px;font-weight:900;color:#fef3c7;letter-spacing:-0.5px;">🥜 GoldKernel</div>
                  <div style="font-size:12px;color:#fde68a;margin-top:4px;text-transform:uppercase;letter-spacing:1px;">Premium Dry Fruits · Nepal</div>
                </td></tr>
                <!-- Body -->
                <tr><td style="padding:36px 32px;">
                  <h2 style="margin:0 0 12px;font-size:20px;color:#1c1917;">Password Reset Request</h2>
                  <p style="color:#44403c;line-height:1.6;margin:0 0 20px;">Hello <strong>{user.username}</strong>,<br>
                  We received a request to reset your password. Click the button below — this link is valid for <strong>30 minutes</strong>.</p>
                  <div style="text-align:center;margin:28px 0;">
                    <a href="{reset_url}" style="background:linear-gradient(135deg,#92400e,#b45309);color:#fff;text-decoration:none;padding:14px 32px;border-radius:9999px;font-weight:700;font-size:15px;display:inline-block;">Reset My Password</a>
                  </div>
                  <p style="color:#78716c;font-size:13px;line-height:1.5;">If the button doesn’t work, copy and paste this link into your browser:<br>
                  <a href="{reset_url}" style="color:#b45309;word-break:break-all;">{reset_url}</a></p>
                  <hr style="border:none;border-top:1px solid #e8e0d4;margin:24px 0;">
                  <p style="color:#a8a29e;font-size:12px;margin:0;">If you didn’t request a password reset, you can safely ignore this email. Your password won’t change.</p>
                </td></tr>
                <!-- Footer -->
                <tr><td style="background:#fef9ee;padding:16px 32px;text-align:center;border-top:1px solid #e8e0d4;">
                  <p style="margin:0;font-size:12px;color:#a8a29e;">© GoldKernel Dry Fruits · Dhangadhi, Kailali, Nepal</p>
                </td></tr>
              </table>
            </td></tr>
          </table>
        </body></html>
        """
        text_body = (
            f"Hello {user.username},\n\n"
            f"Click the link below to reset your password (valid 30 min):\n{reset_url}\n\n"
            f"If you didn't request this, ignore this email.\n\n"
            f"\u2014 GoldKernel Team"
        )
        msg = Message(
            subject="Reset Your GoldKernel Password",
            recipients=[user_email],
            html=html_body,
            body=text_body,
        )
        mail.send(msg)
        logger.info("Password reset email sent to %s", user_email)
        return True
    except Exception as exc:
        logger.error("Failed to send password reset email: %s", exc)
        return False


# ── HTML templates ────────────────────────────────────────────────────────────

def _order_confirmation_html(order, items: list, personalised_tips: str = "") -> str:
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

        {("<div style=\"background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;padding:12px;margin:12px 0\">" + personalised_tips + "</div>") if personalised_tips else ""}
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
