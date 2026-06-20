from datetime import datetime, timezone
from flask_login import UserMixin
from ..extensions import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), nullable=True)   # optional — used for password reset emails
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(10), nullable=False, default="staff")  # 'admin' | 'staff'
    commission_rate = db.Column(db.Numeric(5, 2), default=0.00, nullable=False)
    # Commission rate as percentage (e.g. 2.5 = 2.5% of each sale amount)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    # Relationships
    sales = db.relationship("Sale", back_populates="user")
    purchases = db.relationship("Purchase", back_populates="creator",
                                foreign_keys="Purchase.created_by")
    expenses = db.relationship("Expense", back_populates="creator")
    stock_movements = db.relationship("StockMovement", back_populates="creator")

    def __repr__(self):
        return f"<User {self.username} ({self.role})>"
