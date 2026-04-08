"""Per-user granular permissions."""
from ..extensions import db


class UserPermissions(db.Model):
    __tablename__ = "user_permissions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)

    # Inventory
    can_view_inventory = db.Column(db.Boolean, default=True)
    can_add_product = db.Column(db.Boolean, default=True)
    can_edit_product = db.Column(db.Boolean, default=True)
    can_delete_product = db.Column(db.Boolean, default=False)
    can_adjust_stock = db.Column(db.Boolean, default=True)
    can_bulk_upload_products = db.Column(db.Boolean, default=False)

    # Sales
    can_view_sales = db.Column(db.Boolean, default=True)
    can_create_sale = db.Column(db.Boolean, default=True)
    can_delete_sale = db.Column(db.Boolean, default=False)
    can_give_discount = db.Column(db.Boolean, default=True)
    can_download_invoice = db.Column(db.Boolean, default=True)

    # Purchases
    can_view_purchases = db.Column(db.Boolean, default=True)
    can_create_purchase = db.Column(db.Boolean, default=True)
    can_bulk_upload_purchases = db.Column(db.Boolean, default=False)
    can_manage_suppliers = db.Column(db.Boolean, default=False)

    # Returns
    can_view_returns = db.Column(db.Boolean, default=True)
    can_create_return = db.Column(db.Boolean, default=False)

    # Online Orders
    can_view_online_orders = db.Column(db.Boolean, default=True)
    can_manage_online_orders = db.Column(db.Boolean, default=False)

    # Alerts
    can_view_alerts = db.Column(db.Boolean, default=True)

    # Dashboard
    can_view_dashboard = db.Column(db.Boolean, default=True)

    # Relationships
    user = db.relationship("User", backref=db.backref("permissions", uselist=False))

    @classmethod
    def get_or_create(cls, user_id: int) -> "UserPermissions":
        p = db.session.execute(
            db.select(cls).filter_by(user_id=user_id)
        ).scalar_one_or_none()
        if p is None:
            p = cls(user_id=user_id)
            db.session.add(p)
            db.session.commit()
        return p

    def to_dict(self) -> dict:
        return {c.name: getattr(self, c.name)
                for c in self.__table__.columns
                if c.name not in ("id", "user_id")}
