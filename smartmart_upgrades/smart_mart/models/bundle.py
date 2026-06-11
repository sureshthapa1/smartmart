# smart_mart/models/bundle.py
# ============================
# Gift bundle / hamper model.
# A Bundle is a named product (e.g. "Dashain Premium Hamper")
# that deducts multiple component products from stock when sold.

from smart_mart.extensions import db
import datetime


class Bundle(db.Model):
    __tablename__ = "bundles"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    price       = db.Column(db.Numeric(10, 2), nullable=False)
    image_url   = db.Column(db.String(500), nullable=True)
    is_active   = db.Column(db.Boolean, default=True, nullable=False)
    is_seasonal = db.Column(db.Boolean, default=False, nullable=False)
    season_tag  = db.Column(db.String(50), nullable=True)  # "Dashain", "Tihar", "Wedding"
    created_at  = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    items = db.relationship("BundleItem", back_populates="bundle",
                            cascade="all, delete-orphan")

    @property
    def cost(self):
        return sum(
            (float(i.component.cost_price or 0) * i.quantity)
            for i in self.items if i.component
        )

    @property
    def margin_pct(self):
        c = self.cost
        if c == 0:
            return 100.0
        return round((float(self.price) - c) / float(self.price) * 100, 1)

    def __repr__(self):
        return f"<Bundle {self.name}>"


class BundleItem(db.Model):
    __tablename__ = "bundle_items"

    id           = db.Column(db.Integer, primary_key=True)
    bundle_id    = db.Column(db.Integer, db.ForeignKey("bundles.id"), nullable=False)
    product_id   = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity     = db.Column(db.Numeric(10, 3), nullable=False)  # in grams/units

    bundle    = db.relationship("Bundle", back_populates="items")
    component = db.relationship("Product")

    def __repr__(self):
        return f"<BundleItem {self.product_id} x{self.quantity}>"
