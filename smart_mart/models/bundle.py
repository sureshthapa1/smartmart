from datetime import datetime, timezone

from ..extensions import db


class Bundle(db.Model):
    __tablename__ = "bundles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    is_seasonal = db.Column(db.Boolean, default=False)
    season_tag = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    items = db.relationship(
        "BundleItem",
        back_populates="bundle",
        cascade="all, delete-orphan",
    )

    @property
    def cost(self):
        return sum(
            float(item.component.cost_price or 0) * float(item.quantity or 0)
            for item in self.items
            if item.component
        )

    @property
    def margin_pct(self):
        price = float(self.price or 0)
        if price <= 0:
            return 0
        return ((price - self.cost) / price) * 100


class BundleItem(db.Model):
    __tablename__ = "bundle_items"

    id = db.Column(db.Integer, primary_key=True)
    bundle_id = db.Column(db.Integer, db.ForeignKey("bundles.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Numeric(10, 3), nullable=False)
    bundle = db.relationship("Bundle", back_populates="items")
    component = db.relationship("Product")
