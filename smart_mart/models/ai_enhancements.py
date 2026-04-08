from datetime import datetime, timezone

from ..extensions import db


class LoyaltyWallet(db.Model):
    __tablename__ = "loyalty_wallets"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), unique=True, nullable=False)
    points_balance = db.Column(db.Integer, nullable=False, default=0)
    lifetime_points_earned = db.Column(db.Integer, nullable=False, default=0)
    lifetime_points_redeemed = db.Column(db.Integer, nullable=False, default=0)
    tier = db.Column(db.String(20), nullable=False, default="Silver")
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    customer = db.relationship("Customer")
    transactions = db.relationship(
        "LoyaltyWalletTransaction",
        back_populates="wallet",
        cascade="all, delete-orphan",
    )


class LoyaltyWalletTransaction(db.Model):
    __tablename__ = "loyalty_wallet_transactions"

    id = db.Column(db.Integer, primary_key=True)
    wallet_id = db.Column(db.Integer, db.ForeignKey("loyalty_wallets.id"), nullable=False)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=True)
    points_change = db.Column(db.Integer, nullable=False)
    rupee_value = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    reason = db.Column(db.String(60), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    wallet = db.relationship("LoyaltyWallet", back_populates="transactions")
    sale = db.relationship("Sale")


class CustomerDuplicateFlag(db.Model):
    __tablename__ = "customer_duplicate_flags"

    id = db.Column(db.Integer, primary_key=True)
    primary_customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    duplicate_customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    confidence = db.Column(db.Numeric(5, 4), nullable=False, default=0)
    reason = db.Column(db.String(255), nullable=False)
    suspicious = db.Column(db.Boolean, nullable=False, default=False)
    status = db.Column(db.String(20), nullable=False, default="pending")  # pending|approved|rejected
    suggested_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reviewed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    reviewed_at = db.Column(db.DateTime, nullable=True)

    primary_customer = db.relationship("Customer", foreign_keys=[primary_customer_id])
    duplicate_customer = db.relationship("Customer", foreign_keys=[duplicate_customer_id])


class SyncEvent(db.Model):
    __tablename__ = "sync_events"

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(80), nullable=False)
    entity_type = db.Column(db.String(40), nullable=False)
    entity_id = db.Column(db.String(80), nullable=True)
    operation = db.Column(db.String(20), nullable=False)  # upsert|delete
    payload_json = db.Column(db.Text, nullable=False)
    client_timestamp = db.Column(db.DateTime, nullable=True)
    server_timestamp = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    status = db.Column(db.String(20), nullable=False, default="applied")  # applied|conflict|ignored
    conflict_reason = db.Column(db.String(255), nullable=True)


class DeviceSyncState(db.Model):
    __tablename__ = "device_sync_states"

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(80), nullable=False, unique=True)
    last_event_id = db.Column(db.Integer, nullable=False, default=0)
    last_sync_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class CompetitorPriceEntry(db.Model):
    __tablename__ = "competitor_price_entries"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    competitor_name = db.Column(db.String(120), nullable=False)
    competitor_price = db.Column(db.Numeric(10, 2), nullable=False)
    observed_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    captured_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    notes = db.Column(db.String(255), nullable=True)

    product = db.relationship("Product")


class CompetitorPriceSuggestion(db.Model):
    __tablename__ = "competitor_price_suggestions"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    competitor_entry_id = db.Column(db.Integer, db.ForeignKey("competitor_price_entries.id"), nullable=False)
    current_price = db.Column(db.Numeric(10, 2), nullable=False)
    suggested_price = db.Column(db.Numeric(10, 2), nullable=False)
    confidence = db.Column(db.Numeric(5, 4), nullable=False, default=0)
    rationale = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    product = db.relationship("Product")
    competitor_entry = db.relationship("CompetitorPriceEntry")


class AIDecisionLog(db.Model):
    __tablename__ = "ai_decision_logs"

    id = db.Column(db.Integer, primary_key=True)
    decision_type = db.Column(db.String(60), nullable=False)
    entity_type = db.Column(db.String(60), nullable=False)
    entity_id = db.Column(db.String(80), nullable=True)
    input_snapshot = db.Column(db.Text, nullable=False)
    output_snapshot = db.Column(db.Text, nullable=False)
    confidence = db.Column(db.Numeric(5, 4), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
