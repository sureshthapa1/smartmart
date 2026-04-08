from datetime import datetime, timezone

from ..extensions import db


class CustomerCreditPayment(db.Model):
    __tablename__ = "customer_credit_payments"

    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    payment_mode = db.Column(db.String(20), nullable=False, default="cash")
    note = db.Column(db.String(255), nullable=True)
    paid_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    sale = db.relationship("Sale")
    user = db.relationship("User")


class SupplierPayment(db.Model):
    __tablename__ = "supplier_payments"

    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=False)
    purchase_id = db.Column(db.Integer, db.ForeignKey("purchases.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    payment_mode = db.Column(db.String(20), nullable=False, default="cash")
    note = db.Column(db.String(255), nullable=True)
    paid_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    supplier = db.relationship("Supplier")
    purchase = db.relationship("Purchase")
    user = db.relationship("User")


class CashSession(db.Model):
    __tablename__ = "cash_sessions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    opening_cash = db.Column(db.Numeric(10, 2), nullable=False)
    closing_cash = db.Column(db.Numeric(10, 2), nullable=True)
    expected_cash = db.Column(db.Numeric(10, 2), nullable=True)
    variance = db.Column(db.Numeric(10, 2), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    opened_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    closed_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="open")

    user = db.relationship("User")


class ProductInventoryProfile(db.Model):
    __tablename__ = "product_inventory_profiles"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, unique=True)
    barcode = db.Column(db.String(80), nullable=True, unique=True)
    reorder_level = db.Column(db.Integer, nullable=False, default=10)
    shelf_location = db.Column(db.String(80), nullable=True)

    product = db.relationship("Product")


class ProductBatch(db.Model):
    __tablename__ = "product_batches"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    purchase_id = db.Column(db.Integer, db.ForeignKey("purchases.id"), nullable=True)
    batch_code = db.Column(db.String(80), nullable=False)
    quantity_received = db.Column(db.Integer, nullable=False)
    quantity_remaining = db.Column(db.Integer, nullable=False)
    expiry_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    product = db.relationship("Product")
    purchase = db.relationship("Purchase")


class AppNotification(db.Model):
    __tablename__ = "app_notifications"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    body = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(30), nullable=False, default="info")
    source_type = db.Column(db.String(40), nullable=True)
    source_id = db.Column(db.Integer, nullable=True)
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class CustomerLoyaltyTransaction(db.Model):
    __tablename__ = "customer_loyalty_transactions"

    id = db.Column(db.Integer, primary_key=True)
    customer_name = db.Column(db.String(120), nullable=False)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=True)
    points_change = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    sale = db.relationship("Sale")


class Branch(db.Model):
    __tablename__ = "branches"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(20), nullable=False, unique=True)
    address = db.Column(db.String(255), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
