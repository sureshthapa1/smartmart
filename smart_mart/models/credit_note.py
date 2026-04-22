"""Credit Note model — Task 7."""
from datetime import datetime, timezone
from ..extensions import db


class CreditNote(db.Model):
    __tablename__ = "credit_notes"

    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    reason = db.Column(db.Text, nullable=True)
    issued_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    sale = db.relationship("Sale")
    issuer = db.relationship("User", foreign_keys=[issued_by])

    def __repr__(self):
        return f"<CreditNote #{self.id} sale={self.sale_id} amount={self.amount}>"
