"""Stock Take / Physical Count models — Feature #3."""
from datetime import datetime, timezone
from ..extensions import db


class StockTake(db.Model):
    __tablename__ = "stock_takes"

    STATUS_LABELS = {
        "draft":     ("Draft",     "secondary"),
        "in_progress": ("In Progress", "warning"),
        "completed": ("Completed", "success"),
        "cancelled": ("Cancelled", "danger"),
    }

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(30), nullable=False, unique=True)
    notes = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="draft")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    completed_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    completed_at = db.Column(db.DateTime, nullable=True)

    creator = db.relationship("User", foreign_keys=[created_by])
    completer = db.relationship("User", foreign_keys=[completed_by])
    items = db.relationship("StockTakeItem", back_populates="stock_take", cascade="all, delete-orphan")

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, ("Unknown", "secondary"))

    @property
    def total_variance(self):
        return sum(i.variance for i in self.items)

    @property
    def items_with_variance(self):
        return [i for i in self.items if i.variance != 0]

    def __repr__(self):
        return f"<StockTake #{self.id} {self.reference} {self.status}>"


class StockTakeItem(db.Model):
    __tablename__ = "stock_take_items"

    id = db.Column(db.Integer, primary_key=True)
    stock_take_id = db.Column(db.Integer, db.ForeignKey("stock_takes.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    system_qty = db.Column(db.Integer, nullable=False)   # qty at time of count
    counted_qty = db.Column(db.Integer, nullable=True)   # actual physical count
    note = db.Column(db.String(255), nullable=True)

    stock_take = db.relationship("StockTake", back_populates="items")
    product = db.relationship("Product")

    @property
    def variance(self):
        if self.counted_qty is None:
            return 0
        return self.counted_qty - self.system_qty

    def __repr__(self):
        return f"<StockTakeItem take={self.stock_take_id} product={self.product_id}>"
