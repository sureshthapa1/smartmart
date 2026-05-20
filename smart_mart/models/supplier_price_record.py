from datetime import datetime

from ..extensions import db


class SupplierPriceRecord(db.Model):
    __tablename__ = "supplier_price_records"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    supplier_name = db.Column(db.String(200))
    cost_price = db.Column(db.Numeric(10, 2), nullable=False)
    quantity_kg = db.Column(db.Numeric(10, 3))
    invoice_ref = db.Column(db.String(100))
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)
    recorded_by = db.Column(db.Integer, db.ForeignKey("users.id"))

    product = db.relationship("Product")
    recorder = db.relationship("User")
