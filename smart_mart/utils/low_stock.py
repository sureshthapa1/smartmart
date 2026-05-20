from ..extensions import db
from ..models.product import Product


def get_low_stock_alerts(threshold=500):
    fallback_threshold = int(threshold or 500)
    products = db.session.execute(
        db.select(Product)
        .where(Product.is_active == True)
        .order_by(Product.quantity.asc(), Product.name.asc())
    ).scalars().all()

    alerts = []
    for product in products:
        product_threshold = product.low_stock_threshold
        if product_threshold is None:
            product_threshold = fallback_threshold
        if product.quantity <= product_threshold:
            alerts.append({
                "id": product.id,
                "name": product.name,
                "quantity": product.quantity,
                "threshold": int(product_threshold),
                "severity": "critical" if product.quantity == 0 else "low",
            })

    alerts.sort(key=lambda row: (row["quantity"], row["name"].lower()))
    return alerts
