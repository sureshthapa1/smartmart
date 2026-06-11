# smart_mart/utils/low_stock.py
# ================================
# Call get_low_stock_alerts() anywhere — dashboard, scheduled job, etc.

from smart_mart.extensions import db


DEFAULT_THRESHOLD = 500   # grams or units — adjust per product


def get_low_stock_alerts(threshold: int = DEFAULT_THRESHOLD):
    """
    Returns list of dicts for products at or below threshold.
    Assumes your Product model has: id, name, stock_quantity, low_stock_threshold (optional).
    """
    from smart_mart.models.product import Product  # lazy import to avoid circular

    products = Product.query.filter(Product.is_active == True).all()
    alerts = []
    for p in products:
        limit = getattr(p, "low_stock_threshold", None) or threshold
        qty   = getattr(p, "stock_quantity", 0) or 0
        if qty <= limit:
            alerts.append({
                "id":       p.id,
                "name":     p.name,
                "quantity": qty,
                "threshold": limit,
                "severity": "critical" if qty == 0 else "low",
            })
    alerts.sort(key=lambda x: x["quantity"])
    return alerts


# ── Add low_stock_threshold column to Product model ──────────────────────────
# Run this migration ONCE after adding the column to your Product model:
#
#   flask db migrate -m "add low_stock_threshold to product"
#   flask db upgrade
#
# Add to your Product model class:
#
#   low_stock_threshold = db.Column(db.Integer, nullable=True, default=500)
#
# ── Dashboard usage ──────────────────────────────────────────────────────────
# In your dashboard blueprint route:
#
#   from smart_mart.utils.low_stock import get_low_stock_alerts
#   alerts = get_low_stock_alerts()
#   return render_template("dashboard/index.html", low_stock=alerts, ...)
#
# In dashboard template:
#   {% if low_stock %}
#     <div class="alert alert-warning">
#       ⚠️ {{ low_stock|length }} product(s) running low —
#       {% for a in low_stock[:3] %}{{ a.name }} ({{ a.quantity }}g){% if not loop.last %}, {% endif %}{% endfor %}
#       <a href="{{ url_for('inventory.index') }}">View all</a>
#     </div>
#   {% endif %}
