from ..extensions import db


class ShopSettings(db.Model):
    __tablename__ = "shop_settings"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), default="GoldKernel Dry Fruits & Treats")
    shop_name = db.Column(db.String(120), default="Smart Mart")
    pan_number = db.Column(db.String(50))
    address = db.Column(db.String(255))
    phone = db.Column(db.String(50))
    email = db.Column(db.String(120))
    website = db.Column(db.String(120))
    invoice_prefix = db.Column(db.String(10), default="INV")
    invoice_counter = db.Column(db.Integer, default=1)
    footer_note = db.Column(db.String(255), default="Thank you for shopping with us!")
    vat_enabled = db.Column(db.Boolean, default=False)
    vat_rate = db.Column(db.Numeric(5, 2), default=13.00)  # Nepal standard 13%
    vat_number = db.Column(db.String(50), nullable=True)
    owner_name = db.Column(db.String(120), nullable=True)
    currency_symbol = db.Column(db.String(10), default="NPR")
    low_stock_threshold = db.Column(db.Integer, default=10)
    logo_filename = db.Column(db.String(255), nullable=True)
    logo_data = db.Column(db.Text, nullable=True)  # base64 encoded logo — persists on Render
    # Loyalty programme settings
    loyalty_points_per_rupee = db.Column(db.Numeric(8, 4), default=0.01)   # pts earned per NPR spent (0.01 = 1pt per NPR 100)
    loyalty_rupee_per_point = db.Column(db.Numeric(8, 4), default=1.00)    # NPR discount per point redeemed
    # Social media links (used in store footer)
    facebook_url     = db.Column(db.String(255), nullable=True)
    instagram_url    = db.Column(db.String(255), nullable=True)
    twitter_url      = db.Column(db.String(255), nullable=True)
    tiktok_url       = db.Column(db.String(255), nullable=True)
    whatsapp_number  = db.Column(db.String(30),  nullable=True)   # e.g. 9841234567 (no + or spaces)
    website_url      = db.Column(db.String(255), nullable=True)   # full URL e.g. https://goldkernel.com
    # ── Delivery settings ────────────────────────────────────────
    delivery_charge          = db.Column(db.Numeric(10, 2), default=0)
    free_delivery_above_npr  = db.Column(db.Numeric(10, 2), default=0)  # 0 = never free
    created_at  = db.Column(db.DateTime(timezone=True), default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    updated_at  = db.Column(db.DateTime(timezone=True), default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc), onupdate=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc))

    @classmethod
    def get(cls):
        """Return the single settings row, creating it if it doesn't exist."""
        s = db.session.execute(db.select(cls)).scalar_one_or_none()
        if s is None:
            s = cls()
            db.session.add(s)
            db.session.flush()
        return s

    def next_invoice_number(self) -> str:
        """Return the next invoice number and increment the counter."""
        num = f"{self.invoice_prefix}-{self.invoice_counter:05d}"
        self.invoice_counter += 1
        db.session.flush()  # flush only — caller commits
        return num
