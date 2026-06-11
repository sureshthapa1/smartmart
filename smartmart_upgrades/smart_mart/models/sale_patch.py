# smart_mart/models/sale_patch.py
# ====================================
# PATCH — Add payment_method column to Sale model
# and support Fonepay / eSewa / Khalti tracking.
#
# STEP 1: Add this field to your existing Sale model:
#
#   PAYMENT_METHODS = [
#       ("cash",    "Cash"),
#       ("fonepay", "Fonepay"),
#       ("esewa",   "eSewa"),
#       ("khalti",  "Khalti"),
#       ("qr",      "QR Code"),
#       ("bank",    "Bank Transfer"),
#       ("credit",  "Credit / Udharo"),
#   ]
#   payment_method = db.Column(db.String(20), nullable=False, default="cash")
#
# STEP 2: Create and run migration:
#   flask db migrate -m "add payment_method to sales"
#   flask db upgrade
#
# STEP 3: In your POS checkout form, add the selector:
#
#   <select name="payment_method" class="form-select" required>
#     <option value="cash">Cash</option>
#     <option value="fonepay">Fonepay</option>
#     <option value="esewa">eSewa</option>
#     <option value="khalti">Khalti</option>
#     <option value="qr">QR Code</option>
#     <option value="bank">Bank Transfer</option>
#     <option value="credit">Credit / Udharo</option>
#   </select>
#
# STEP 4: Daily reconciliation query (add to your reports blueprint):

DAILY_PAYMENT_SUMMARY_SQL = """
SELECT
    payment_method,
    COUNT(*)          AS num_sales,
    SUM(total_amount) AS total_collected
FROM sales
WHERE DATE(created_at) = :today
GROUP BY payment_method
ORDER BY total_collected DESC;
"""

# This query gives you a daily breakdown like:
# | Payment     | Sales | Total (NPR) |
# | cash        |  24   |  45,200     |
# | fonepay     |   8   |  18,400     |
# | esewa       |   5   |  11,000     |
# | khalti      |   3   |   6,500     |
#
# Use it for daily cash drawer reconciliation —
# you know exactly how much physical cash you should have,
# and how much is in each digital wallet.


def get_payment_summary(date=None):
    """Call from reports or dashboard to get payment breakdown."""
    import datetime
    from smart_mart.extensions import db

    target = date or datetime.date.today()
    rows   = db.session.execute(
        db.text(DAILY_PAYMENT_SUMMARY_SQL),
        {"today": target},
    ).fetchall()
    return [
        {
            "method":        r[0],
            "num_sales":     r[1],
            "total":         float(r[2] or 0),
        }
        for r in rows
    ]
