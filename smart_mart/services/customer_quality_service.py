from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher

from ..extensions import db
from ..models.ai_enhancements import CustomerDuplicateFlag
from ..models.customer import Customer
from .ai_decision_logger import log_decision


def _normalize_phone(phone: str | None) -> str:
    if not phone:
        return ""
    return "".join(ch for ch in phone if ch.isdigit())


def _name_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def detect_duplicates(trigger_user_id: int | None = None) -> list[CustomerDuplicateFlag]:
    customers = db.session.execute(db.select(Customer).order_by(Customer.id)).scalars().all()
    created: list[CustomerDuplicateFlag] = []

    for i, primary in enumerate(customers):
        for duplicate in customers[i + 1:]:
            if primary.id == duplicate.id:
                continue

            phone_primary = _normalize_phone(primary.phone)
            phone_duplicate = _normalize_phone(duplicate.phone)
            same_phone = bool(phone_primary and phone_primary == phone_duplicate)
            name_score = _name_similarity(primary.name, duplicate.name)
            suspicious = False
            reason = None
            confidence = 0.0

            if same_phone:
                confidence = max(confidence, 0.99)
                reason = "Same phone number"
            elif name_score >= 0.92:
                confidence = max(confidence, name_score)
                reason = f"Very similar names ({name_score:.2f})"
            elif name_score >= 0.82 and (phone_primary[:7] == phone_duplicate[:7] and phone_primary):
                confidence = max(confidence, 0.85)
                reason = "Similar name and near-matching phone pattern"

            if any(ch.isdigit() for ch in (primary.name or "")) or len(phone_primary) not in (0, 10, 11, 12):
                suspicious = True

            if reason is None:
                continue

            existing = db.session.execute(
                db.select(CustomerDuplicateFlag).where(
                    CustomerDuplicateFlag.primary_customer_id == primary.id,
                    CustomerDuplicateFlag.duplicate_customer_id == duplicate.id,
                    CustomerDuplicateFlag.status == "pending",
                )
            ).scalar_one_or_none()
            if existing:
                continue

            flag = CustomerDuplicateFlag(
                primary_customer_id=primary.id,
                duplicate_customer_id=duplicate.id,
                confidence=confidence,
                reason=reason,
                suspicious=suspicious,
                suggested_by_user_id=trigger_user_id,
            )
            db.session.add(flag)
            created.append(flag)

            log_decision(
                decision_type="duplicate_customer_detection",
                entity_type="customer_pair",
                entity_id=f"{primary.id}:{duplicate.id}",
                input_snapshot={
                    "primary_name": primary.name,
                    "duplicate_name": duplicate.name,
                    "primary_phone": primary.phone,
                    "duplicate_phone": duplicate.phone,
                },
                output_snapshot={
                    "reason": reason,
                    "confidence": confidence,
                    "suspicious": suspicious,
                },
                confidence=confidence,
            )

    db.session.commit()
    return created


def list_duplicate_flags(status: str = "pending") -> list[CustomerDuplicateFlag]:
    stmt = db.select(CustomerDuplicateFlag).order_by(CustomerDuplicateFlag.created_at.desc())
    if status:
        stmt = stmt.where(CustomerDuplicateFlag.status == status)
    return db.session.execute(stmt).scalars().all()


def approve_merge(flag_id: int, admin_user_id: int) -> CustomerDuplicateFlag:
    flag = db.get_or_404(CustomerDuplicateFlag, flag_id)
    if flag.status != "pending":
        raise ValueError("This duplicate flag has already been reviewed.")

    primary = db.get_or_404(Customer, flag.primary_customer_id)
    duplicate = db.get_or_404(Customer, flag.duplicate_customer_id)

    primary.visit_count = (primary.visit_count or 0) + (duplicate.visit_count or 0)
    if not primary.phone and duplicate.phone:
        primary.phone = duplicate.phone
    if not primary.address and duplicate.address:
        primary.address = duplicate.address
    if duplicate.last_visit and (not primary.last_visit or duplicate.last_visit > primary.last_visit):
        primary.last_visit = duplicate.last_visit

    db.session.delete(duplicate)
    flag.status = "approved"
    flag.reviewed_by_user_id = admin_user_id
    flag.reviewed_at = datetime.now(timezone.utc)
    db.session.commit()
    return flag


def reject_merge(flag_id: int, admin_user_id: int) -> CustomerDuplicateFlag:
    flag = db.get_or_404(CustomerDuplicateFlag, flag_id)
    if flag.status != "pending":
        raise ValueError("This duplicate flag has already been reviewed.")
    flag.status = "rejected"
    flag.reviewed_by_user_id = admin_user_id
    flag.reviewed_at = datetime.now(timezone.utc)
    db.session.commit()
    return flag
