"""Per-user granular permissions."""
from ..extensions import db


class UserPermissions(db.Model):
    __tablename__ = "user_permissions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)

    # ── Inventory ─────────────────────────────────────────────────────────
    can_view_inventory = db.Column(db.Boolean, default=True)
    can_add_product = db.Column(db.Boolean, default=False)
    can_edit_product = db.Column(db.Boolean, default=False)
    can_delete_product = db.Column(db.Boolean, default=False)
    can_adjust_stock = db.Column(db.Boolean, default=False)
    can_bulk_upload_products = db.Column(db.Boolean, default=False)
    can_manage_categories = db.Column(db.Boolean, default=False)
    can_manage_variants = db.Column(db.Boolean, default=False)
    can_print_labels = db.Column(db.Boolean, default=False)
    can_view_stock_take = db.Column(db.Boolean, default=False)
    can_manage_stock_take = db.Column(db.Boolean, default=False)

    # ── Sales ─────────────────────────────────────────────────────────────
    can_view_sales = db.Column(db.Boolean, default=True)
    can_create_sale = db.Column(db.Boolean, default=True)
    can_delete_sale = db.Column(db.Boolean, default=False)
    can_give_discount = db.Column(db.Boolean, default=False)
    can_download_invoice = db.Column(db.Boolean, default=True)
    can_view_customer_statement = db.Column(db.Boolean, default=False)

    # ── Returns ───────────────────────────────────────────────────────────
    can_view_returns = db.Column(db.Boolean, default=True)
    can_create_return = db.Column(db.Boolean, default=False)
    can_view_supplier_returns = db.Column(db.Boolean, default=False)
    can_manage_supplier_returns = db.Column(db.Boolean, default=False)

    # ── Purchases ─────────────────────────────────────────────────────────
    can_view_purchases = db.Column(db.Boolean, default=False)
    can_create_purchase = db.Column(db.Boolean, default=False)
    can_bulk_upload_purchases = db.Column(db.Boolean, default=False)
    can_manage_suppliers = db.Column(db.Boolean, default=False)
    can_view_purchase_orders = db.Column(db.Boolean, default=False)
    can_manage_purchase_orders = db.Column(db.Boolean, default=False)

    # ── Customers ─────────────────────────────────────────────────────────
    can_view_customers = db.Column(db.Boolean, default=False)
    can_manage_customers = db.Column(db.Boolean, default=False)

    # ── Finance / Operations ──────────────────────────────────────────────
    can_manage_credits = db.Column(db.Boolean, default=False)
    can_manage_cash_session = db.Column(db.Boolean, default=False)
    can_view_expenses = db.Column(db.Boolean, default=False)
    can_manage_expenses = db.Column(db.Boolean, default=False)

    # ── Reports ───────────────────────────────────────────────────────────
    can_view_reports = db.Column(db.Boolean, default=False)
    can_view_sales_report = db.Column(db.Boolean, default=False)
    can_view_profit_report = db.Column(db.Boolean, default=False)
    can_view_stock_report = db.Column(db.Boolean, default=False)
    can_view_credit_report = db.Column(db.Boolean, default=False)

    # ── Online Orders ─────────────────────────────────────────────────────
    can_view_online_orders = db.Column(db.Boolean, default=False)
    can_manage_online_orders = db.Column(db.Boolean, default=False)

    # ── Promotions ────────────────────────────────────────────────────────
    can_view_promotions = db.Column(db.Boolean, default=False)
    can_manage_promotions = db.Column(db.Boolean, default=False)

    # ── Transfers ─────────────────────────────────────────────────────────
    can_view_transfers = db.Column(db.Boolean, default=False)
    can_manage_transfers = db.Column(db.Boolean, default=False)

    # ── AI & Advisor ──────────────────────────────────────────────────────
    can_view_ai_insights = db.Column(db.Boolean, default=False)
    can_view_advisor = db.Column(db.Boolean, default=False)

    # ── BI Module ─────────────────────────────────────────────────────────
    can_view_bi_dashboard = db.Column(db.Boolean, default=False)
    can_manage_bi_batches = db.Column(db.Boolean, default=False)

    # ── Offers & Retention ────────────────────────────────────────────────
    can_view_offers = db.Column(db.Boolean, default=True)
    can_manage_offers = db.Column(db.Boolean, default=False)
    can_assign_offers = db.Column(db.Boolean, default=True)
    can_apply_offers = db.Column(db.Boolean, default=True)

    # ── Alerts & Dashboard ────────────────────────────────────────────────
    can_view_alerts = db.Column(db.Boolean, default=True)
    can_view_dashboard = db.Column(db.Boolean, default=True)

    # ── Relationships ─────────────────────────────────────────────────────
    user = db.relationship("User", backref=db.backref("permissions", uselist=False))

    @classmethod
    def get_or_create(cls, user_id: int) -> "UserPermissions":
        p = db.session.execute(
            db.select(cls).filter_by(user_id=user_id)
        ).scalar_one_or_none()
        if p is None:
            p = cls(user_id=user_id)
            db.session.add(p)
            db.session.flush()
        return p

    def to_dict(self) -> dict:
        return {c.name: getattr(self, c.name)
                for c in self.__table__.columns
                if c.name not in ("id", "user_id")}
