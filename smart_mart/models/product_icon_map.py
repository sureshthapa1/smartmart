"""Custom emoji icon per product name — persisted so it's reused automatically."""
from ..extensions import db


class ProductIconMap(db.Model):
    __tablename__ = "product_icon_map"

    id = db.Column(db.Integer, primary_key=True)
    product_name_lower = db.Column(db.String(120), unique=True, nullable=False)
    emoji = db.Column(db.String(10), nullable=False)

    @classmethod
    def get(cls, name: str) -> str | None:
        row = db.session.execute(
            db.select(cls).filter_by(product_name_lower=name.strip().lower())
        ).scalar_one_or_none()
        return row.emoji if row else None

    @classmethod
    def set(cls, name: str, emoji: str):
        existing = db.session.execute(
            db.select(cls).filter_by(product_name_lower=name.strip().lower())
        ).scalar_one_or_none()
        if existing:
            existing.emoji = emoji
        else:
            db.session.add(cls(product_name_lower=name.strip().lower(), emoji=emoji))
        db.session.flush()
