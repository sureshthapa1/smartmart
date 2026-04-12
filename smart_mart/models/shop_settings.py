from ..extensions import db


class ShopSettings(db.Model):
    __tablename__ = "shop_settings"

    id = db.Column(db.Integer, primary_key=True)
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
    currency_symbol = db.Column(db.String(10), default="NPR")
    low_stock_threshold = db.Column(db.Integer, default=10)
    logo_filename = db.Column(db.String(255), nullable=True)
    # Loyalty programme settings
    loyalty_points_per_rupee = db.Column(db.Numeric(8, 4), default=0.01)   # pts earned per NPR spent (0.01 = 1pt per NPR 100)
    loyalty_rupee_per_point = db.Column(db.Numeric(8, 4), default=1.00)    # NPR discount per point redeemed

    @classmethod
    def get(cls):
        """Return the single settings row, creating it if it doesn't exist."""
        s = db.session.execute(db.select(cls)).scalar_one_or_none()
        if s is None:
            s = cls()
            db.session.add(s)
            db.session.commit()
        return s

    def next_invoice_number(self) -> str:
        """Return the next invoice number and increment the counter."""
        num = f"{self.invoice_prefix}-{self.invoice_counter:05d}"
        self.invoice_counter += 1
        db.session.commit()
        return num
