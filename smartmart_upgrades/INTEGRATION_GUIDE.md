# GoldKernel SmartMart — Upgrade Integration Guide
# ==================================================
# Follow these steps IN ORDER. Each section tells you
# exactly which file to touch and what to do.

## STEP 1 — Replace requirements.txt
Copy `requirements.txt` from this package → overwrite your repo's `requirements.txt`.
Copy `requirements-dev.txt` → add to repo root.

## STEP 2 — Replace extensions.py
Copy `smart_mart/extensions.py` → overwrite your existing file.
This adds: Flask-Migrate (migrations), Flask-Limiter (rate limiting).

## STEP 3 — Register new extensions in app.py
In your `smart_mart/app.py`, inside `create_app()`, ADD these lines
alongside your existing `db.init_app(app)` block:

    from smart_mart.extensions import migrate, limiter
    from smart_mart.utils.nepali_date import ad_to_bs_filter
    from smart_mart.utils.low_stock import get_low_stock_alerts

    migrate.init_app(app, db)
    limiter.init_app(app)
    app.jinja_env.filters["bs_date"] = ad_to_bs_filter

Also REGISTER the new blueprints:

    from smart_mart.blueprints.bundles.routes import bundles_bp
    from smart_mart.blueprints.ai_chat.routes import ai_chat_bp
    app.register_blueprint(bundles_bp)
    app.register_blueprint(ai_chat_bp)

Add DB safety check at the bottom of create_app():

    from smart_mart.app_patch_notes import _check_db_safety
    _check_db_safety(app)

## STEP 4 — Copy new model files
Copy `smart_mart/models/bundle.py` → your `smart_mart/models/` folder.

In your existing `Sale` model, add this field:

    PAYMENT_METHODS = [
        ("cash","Cash"), ("fonepay","Fonepay"), ("esewa","eSewa"),
        ("khalti","Khalti"), ("qr","QR Code"), ("bank","Bank Transfer"),
        ("credit","Credit / Udharo"),
    ]
    payment_method = db.Column(db.String(20), nullable=False, default="cash")

In your existing `Product` model, add:

    low_stock_threshold = db.Column(db.Integer, nullable=True, default=500)

## STEP 5 — Copy utility files
Copy everything from `smart_mart/utils/` → your `smart_mart/utils/` folder:
- nepali_date.py
- expiry_check.py
- vat_invoice.py
- low_stock.py
- cash_flow_forecast.py

## STEP 6 — Copy blueprint folders
Copy `smart_mart/blueprints/bundles/` → your blueprints folder.
Copy `smart_mart/blueprints/ai_chat/` → your blueprints folder.
Make sure each has an `__init__.py` (create empty files if needed).

## STEP 7 — Copy templates
Copy `templates/bundles/` → your `smart_mart/templates/` folder.
Copy `templates/ai_chat/` → your `smart_mart/templates/` folder.

## STEP 8 — Auth rate limiting
Merge `smart_mart/blueprints/auth/routes_patch.py` into your existing auth routes.
Find your login route and wrap it with:

    @limiter.limit("5 per minute")
    def login():
        ...

## STEP 9 — Expiry check at POS checkout
In your POS checkout route, add:

    from smart_mart.utils.expiry_check import check_cart_for_expiry, has_blocking_expiry
    issues = check_cart_for_expiry(cart_items)
    if has_blocking_expiry(issues):
        for i in issues:
            flash(i.message, "danger")
        return redirect(url_for("pos.cart"))

## STEP 10 — VAT Invoice
In your sales/invoice route, add a PDF download option:

    from smart_mart.utils.vat_invoice import generate_vat_invoice
    from flask import send_file
    from io import BytesIO

    @sales_bp.route("/<int:sale_id>/invoice/pdf")
    @login_required
    def invoice_pdf(sale_id):
        sale = Sale.query.get_or_404(sale_id)
        shop = ShopSettings.query.first()
        pdf  = generate_vat_invoice(sale, shop)
        return send_file(BytesIO(pdf), mimetype="application/pdf",
                         download_name=f"GoldKernel_Invoice_{sale_id}.pdf")

## STEP 11 — Voice assistant (add to base template)
In your `templates/base.html`, before `</body>`, add:

    <script src="{{ url_for('static', filename='js/voice_assistant.js') }}"></script>

Copy `smart_mart/static/js/voice_assistant.js` → your static/js folder.

## STEP 12 — Low stock on dashboard
In your dashboard route:

    from smart_mart.utils.low_stock import get_low_stock_alerts
    low_stock = get_low_stock_alerts()
    return render_template("dashboard/index.html", low_stock=low_stock, ...)

In your dashboard template, add:

    {% if low_stock %}
    <div class="alert alert-warning">
      ⚠️ {{ low_stock|length }} product(s) running low —
      {% for a in low_stock[:3] %}{{ a.name }} ({{ a.quantity }}g){% endfor %}
      <a href="{{ url_for('inventory.index') }}">View all →</a>
    </div>
    {% endif %}

## STEP 13 — Set environment variables on Render
In your Render dashboard → Environment → Add these:

    ANTHROPIC_API_KEY   = sk-ant-...   (get from console.anthropic.com)
    FLASK_ENV           = production
    SECRET_KEY          = (generate: python -c "import secrets; print(secrets.token_hex(32))")

## STEP 14 — Run database migrations
After pushing to GitHub and Render deploys, run in Render Shell:

    flask db init          # only first time
    flask db migrate -m "add bundle, payment_method, low_stock_threshold"
    flask db upgrade

## STEP 15 — Add BS date filter to your report templates
Anywhere you show a date, use:

    {{ sale.created_at | bs_date }}    {# outputs: "15 Baishakh 2082 BS" #}

## STEP 16 — Add bundles to navigation
In your nav template, add:

    <a href="{{ url_for('bundles.index') }}" class="nav-link">
      🎁 Gift Bundles
    </a>

## STEP 17 — Add AI chat to navigation
    <a href="{{ url_for('ai_chat.index') }}" class="nav-link">
      🤖 AI Advisor
    </a>

---
## Summary of what's new after all steps:

✅ Rate limiting on login (5 attempts / minute)
✅ SQLite production safety warning
✅ Expiry date blocked at POS for expired products
✅ Flask-Migrate — proper DB migrations going forward
✅ Nepali BS calendar filter for all templates
✅ Nepal VAT (13%) PDF invoice download
✅ Low stock alerts on dashboard
✅ Payment method tracking (Cash/Fonepay/eSewa/Khalti/QR)
✅ Gift bundle / hamper creation and sale with stock deduction
✅ AI Business Advisor chatbot (Claude-powered, live data)
✅ Voice assistant (browser Web Speech API, no extra cost)
✅ 30-day cash flow forecast with Dashain/Tihar multipliers
✅ requirements-dev.txt split from production requirements
