from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class ExpiryIssue:
    product_id: int
    product_name: str
    expiry_date: date
    severity: str
    message: str


def check_cart_for_expiry(cart_items):
    today = date.today()
    warning_cutoff = today + timedelta(days=7)
    issues: list[ExpiryIssue] = []

    for item in cart_items:
        expiry_date = getattr(item, "expiry_date", None)
        if not expiry_date:
            continue

        product_id = getattr(item, "product_id", None)
        product_name = getattr(item, "product_name", None) or "Product"
        if expiry_date < today:
            issues.append(ExpiryIssue(
                product_id=product_id,
                product_name=product_name,
                expiry_date=expiry_date,
                severity="expired",
                message=f"{product_name} expired on {expiry_date.isoformat()}. Sale blocked.",
            ))
        elif expiry_date <= warning_cutoff:
            issues.append(ExpiryIssue(
                product_id=product_id,
                product_name=product_name,
                expiry_date=expiry_date,
                severity="warning",
                message=f"{product_name} expires soon on {expiry_date.isoformat()}.",
            ))

    return issues
