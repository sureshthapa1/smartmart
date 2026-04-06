from datetime import datetime, timezone
from ..extensions import db


class Supplier(db.Model):
    __tablename__ = "suppliers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    contact = db.Column(db.String(120))
    email = db.Column(db.String(120))
    address = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    products = db.relationship("Product", back_populates="supplier", lazy="dynamic")
    purchases = db.relationship("Purchase", back_populates="supplier", lazy="dynamic")

    def __repr__(self):
        return f"<Supplier {self.name}>"
