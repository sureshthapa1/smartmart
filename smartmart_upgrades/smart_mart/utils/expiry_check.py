# smart_mart/utils/expiry_check.py
# ==================================
# Call check_cart_for_expiry(cart_items) before finalising any POS sale.
# Returns a list of warnings/errors the route should handle.

import datetime
from dataclasses import dataclass
from typing import List


@dataclass
class ExpiryIssue:
    product_id: int
    product_name: str
    expiry_date: datetime.date
    severity: str   # "expired" | "warning" (expires within 7 days)
    message: str


def check_cart_for_expiry(cart_items) -> List[ExpiryIssue]:
    """
    cart_items: iterable of objects with:
        .product_id, .product_name, .expiry_date (date or None)

    Returns list of ExpiryIssue.
    Empty list = all clear, safe to proceed.
    """
    issues = []
    today = datetime.date.today()
    warning_threshold = today + datetime.timedelta(days=7)

    for item in cart_items:
        exp = getattr(item, "expiry_date", None)
        if exp is None:
            continue  # No expiry set — skip

        if isinstance(exp, datetime.datetime):
            exp = exp.date()

        if exp < today:
            issues.append(ExpiryIssue(
                product_id=item.product_id,
                product_name=item.product_name,
                expiry_date=exp,
                severity="expired",
                message=f"'{item.product_name}' expired on {exp.strftime('%d %b %Y')}. Remove from cart.",
            ))
        elif exp <= warning_threshold:
            issues.append(ExpiryIssue(
                product_id=item.product_id,
                product_name=item.product_name,
                expiry_date=exp,
                severity="warning",
                message=f"'{item.product_name}' expires on {exp.strftime('%d %b %Y')} — expiring soon.",
            ))

    return issues


def has_blocking_expiry(issues: List[ExpiryIssue]) -> bool:
    """Returns True if any item is already expired (should block the sale)."""
    return any(i.severity == "expired" for i in issues)


# ── Usage in your POS blueprint ──────────────────────────────────────────────
#
# from smart_mart.utils.expiry_check import check_cart_for_expiry, has_blocking_expiry
#
# @pos_bp.route("/checkout", methods=["POST"])
# def checkout():
#     cart = build_cart_from_session()           # your existing function
#     issues = check_cart_for_expiry(cart)
#
#     if has_blocking_expiry(issues):
#         for issue in issues:
#             flash(issue.message, "danger")
#         return redirect(url_for("pos.cart"))   # send back to cart
#
#     # optional: flash warnings for near-expiry but allow sale
#     for issue in issues:
#         if issue.severity == "warning":
#             flash(issue.message, "warning")
#
#     # ... proceed with sale as normal
