from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN

from ..extensions import db
from ..models.ai_enhancements import LoyaltyWallet, LoyaltyWalletTransaction
from ..models.customer import Customer
from .ai_decision_logger import log_decision

POINTS_RUPEE_DIVISOR = Decimal("100")  # 1 point per NPR 100 purchase
REDEEM_VALUE_PER_POINT = Decimal("1")  # 1 point = NPR 1 discount


def _tier_for_lifetime_points(points: int) -> str:
    if points >= 10000:
        return "Platinum"
    if points >= 3000:
        return "Gold"
    return "Silver"


def _wallet_for_customer(customer: Customer) -> LoyaltyWallet:
    wallet = db.session.execute(
        db.select(LoyaltyWallet).where(LoyaltyWallet.customer_id == customer.id)
    ).scalar_one_or_none()
    if wallet is None:
        wallet = LoyaltyWallet(customer_id=customer.id)
        db.session.add(wallet)
        db.session.flush()
    return wallet


def get_or_create_wallet(customer_name: str, customer_phone: str | None = None) -> LoyaltyWallet | None:
    if not customer_name or customer_name.strip().lower() in ("", "walk-in customer"):
        return None
    customer = db.session.execute(
        db.select(Customer).where(db.func.lower(Customer.name) == customer_name.strip().lower())
    ).scalar_one_or_none()
    if customer is None:
        customer = Customer(name=customer_name.strip(), phone=customer_phone or None, visit_count=0)
        db.session.add(customer)
        db.session.flush()
    return _wallet_for_customer(customer)


def wallet_snapshot(wallet: LoyaltyWallet | None) -> dict:
    if wallet is None:
        return {"available": False}
    return {
        "available": True,
        "wallet_id": wallet.id,
        "customer_id": wallet.customer_id,
        "points_balance": wallet.points_balance,
        "lifetime_points_earned": wallet.lifetime_points_earned,
        "lifetime_points_redeemed": wallet.lifetime_points_redeemed,
        "tier": wallet.tier,
    }


def preview_redeem(wallet: LoyaltyWallet | None, requested_points: int, gross_total: float) -> dict:
    if wallet is None or requested_points <= 0:
        return {"redeemed_points": 0, "discount": 0.0, "payable_total": float(gross_total)}
    redeemable = min(int(wallet.points_balance), int(requested_points))
    raw_discount = Decimal(redeemable) * REDEEM_VALUE_PER_POINT
    capped_discount = min(raw_discount, Decimal(str(gross_total)))
    discounted_points = int(capped_discount // REDEEM_VALUE_PER_POINT)
    payable = Decimal(str(gross_total)) - capped_discount
    return {
        "redeemed_points": discounted_points,
        "discount": float(capped_discount),
        "payable_total": float(payable),
    }


def apply_sale_points(
    wallet: LoyaltyWallet | None,
    sale_id: int,
    final_amount_paid: float,
    redeemed_points: int = 0,
):
    if wallet is None:
        return

    earned_points = int((Decimal(str(final_amount_paid)) / POINTS_RUPEE_DIVISOR).quantize(Decimal("1"), rounding=ROUND_DOWN))
    redeemed_points = max(0, min(int(redeemed_points), int(wallet.points_balance)))

    if redeemed_points > 0:
        wallet.points_balance -= redeemed_points
        wallet.lifetime_points_redeemed += redeemed_points
        db.session.add(
            LoyaltyWalletTransaction(
                wallet_id=wallet.id,
                sale_id=sale_id,
                points_change=-redeemed_points,
                rupee_value=Decimal(redeemed_points) * REDEEM_VALUE_PER_POINT,
                reason="billing_redeem",
            )
        )

    if earned_points > 0:
        wallet.points_balance += earned_points
        wallet.lifetime_points_earned += earned_points
        db.session.add(
            LoyaltyWalletTransaction(
                wallet_id=wallet.id,
                sale_id=sale_id,
                points_change=earned_points,
                rupee_value=0,
                reason="purchase_earn",
            )
        )

    wallet.tier = _tier_for_lifetime_points(wallet.lifetime_points_earned)
    wallet.updated_at = datetime.now(timezone.utc)

    log_decision(
        decision_type="loyalty_points_update",
        entity_type="wallet",
        entity_id=wallet.id,
        input_snapshot={
            "sale_id": sale_id,
            "final_amount_paid": final_amount_paid,
            "redeemed_points": redeemed_points,
        },
        output_snapshot={
            "earned_points": earned_points,
            "new_balance": wallet.points_balance,
            "tier": wallet.tier,
        },
        confidence=1.0,
    )


def redeem_points_manual(wallet_id: int, points: int, reason: str):
    wallet = db.get_or_404(LoyaltyWallet, wallet_id)
    points = int(points)
    if points <= 0:
        raise ValueError("Points must be greater than zero.")
    if points > wallet.points_balance:
        raise ValueError("Insufficient wallet points.")
    wallet.points_balance -= points
    wallet.lifetime_points_redeemed += points
    wallet.updated_at = datetime.now(timezone.utc)
    db.session.add(
        LoyaltyWalletTransaction(
            wallet_id=wallet.id,
            points_change=-points,
            rupee_value=Decimal(points) * REDEEM_VALUE_PER_POINT,
            reason=reason or "manual_redeem",
        )
    )
    db.session.commit()
