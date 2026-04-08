"""Stock Transfer between branches."""
from datetime import datetime, timezone
from ..extensions import db


class StockTransfer(db.Model):
    __tablename__ = "stock_transfers"

    id = db.Column(db.Integer, primary_key=True)
    from_branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=False)
    to_branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")  # pending|completed|cancelled
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    completed_at = db.Column(db.DateTime, nullable=True)

    from_branch = db.relationship("Branch", foreign_keys=[from_branch_id], backref="transfers_out")
    to_branch = db.relationship("Branch", foreign_keys=[to_branch_id], backref="transfers_in")
    creator = db.relationship("User", foreign_keys=[created_by])
    items = db.relationship("StockTransferItem", back_populates="transfer", cascade="all, delete-orphan")

    @property
    def total_items(self):
        return sum(i.quantity for i in self.items)


class StockTransferItem(db.Model):
    __tablename__ = "stock_transfer_items"

    id = db.Column(db.Integer, primary_key=True)
    transfer_id = db.Column(db.Integer, db.ForeignKey("stock_transfers.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)

    transfer = db.relationship("StockTransfer", back_populates="items")
    product = db.relationship("Product")
