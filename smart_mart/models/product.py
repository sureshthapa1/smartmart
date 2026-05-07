from datetime import datetime, timezone
from ..extensions import db


class Product(db.Model):
    __tablename__ = "products"
    __table_args__ = (
        db.Index("ix_product_sku", "sku"),
        db.Index("ix_product_quantity", "quantity"),
        db.Index("ix_product_category", "category"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(80))
    sku = db.Column(db.String(80), unique=True, nullable=False)
    barcode = db.Column(db.String(80), nullable=True)  # EAN-13/UPC manufacturer barcode
    cost_price = db.Column(db.Numeric(10, 2), nullable=False)
    selling_price = db.Column(db.Numeric(10, 2), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=0)
    inventory_value = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=True)
    expiry_date = db.Column(db.Date, nullable=True)
    image_filename = db.Column(db.String(255), nullable=True)
    unit = db.Column(db.String(20), nullable=True, default="pcs")
    reorder_point = db.Column(db.Integer, nullable=True, default=10)  # MOQ alert threshold
    is_active = db.Column(db.Boolean, nullable=False, default=True)  # False = discontinued
    max_discount_pct = db.Column(db.Numeric(5, 2), nullable=True)    # max % discount allowed
    tax_category = db.Column(db.String(20), nullable=True, default="standard")  # standard|exempt|reduced
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    supplier = db.relationship("Supplier", back_populates="products")
    sale_items = db.relationship("SaleItem", back_populates="product")
    purchase_items = db.relationship("PurchaseItem", back_populates="product")
    stock_movements = db.relationship("StockMovement", back_populates="product")

    def __repr__(self):
        return f"<Product {self.sku} - {self.name}>"
