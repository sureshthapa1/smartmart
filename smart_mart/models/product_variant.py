"""Product variants — size/unit/colour variants under a parent product."""
from datetime import datetime, timezone
from ..extensions import db


class ProductVariant(db.Model):
    __tablename__ = "product_variants"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    variant_name = db.Column(db.String(80), nullable=False)   # e.g. "1kg", "Red", "Large", "1 Piece"
    sku = db.Column(db.String(80), unique=True, nullable=False)
    cost_price = db.Column(db.Numeric(10, 2), nullable=False)
    selling_price = db.Column(db.Numeric(10, 2), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=0)
    barcode = db.Column(db.String(80), nullable=True, unique=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    # ── Pack splitting support ────────────────────────────────────────────
    # units_per_parent: how many of THIS variant are in one parent product unit.
    # e.g. for "Chocolate – 1 Piece" under "Chocolate Box" set to 24 if a box has 24 pieces.
    # When you run "Split Pack", 1 parent unit is deducted and units_per_parent
    # pieces are added to this variant's quantity.
    units_per_parent = db.Column(db.Integer, nullable=True, default=None)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    product = db.relationship("Product", backref=db.backref("variants", lazy="dynamic"))

    def __repr__(self):
        return f"<ProductVariant {self.sku} - {self.variant_name}>"
