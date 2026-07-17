"""Website/POS integration logic for GoldKernel e-commerce."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func

from ..extensions import db
from ..models.customer import Customer
from ..models.ecommerce import EcommercePayment, StockReservation, SyncLog
from ..models.online_order import OnlineOrder, OnlineOrderItem
from ..models.product import Product
from ..models.shop_settings import ShopSettings


WEB_TO_POS_STATUS = {
    "pending": "pending",
    "confirmed": "confirmed",
    "packed": "preparing",
    "shipped": "out_for_delivery",
    "delivered": "delivered",
    "cancelled": "cancelled",
}

POS_TO_WEB_STATUS = {
    "pending": "pending",
    "confirmed": "confirmed",
    "preparing": "packed",
    "out_for_delivery": "shipped",
    "delivered": "delivered",
    "cancelled": "cancelled",
    "returned": "cancelled",
}

CONFIRMED_POS_STATUSES = {"confirmed", "preparing", "out_for_delivery", "delivered"}
TERMINAL_POS_STATUSES = {"delivered", "cancelled", "returned"}
PAYMENT_METHODS = {"cod", "esewa", "khalti", "online", "card", "qr"}
PAYMENT_STATUSES = {"pending", "paid", "failed", "refunded"}


class EcommerceSyncError(ValueError):
    def __init__(self, message: str, status_code: int = 400, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def money(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value if value is not None else default)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise EcommerceSyncError(f"Invalid money value: {value!r}")


def clamp_reservation_minutes(value: Any) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = 30
    return max(15, min(minutes, 60))


def generate_order_number() -> str:
    try:
        settings = ShopSettings.get()
        prefix = (settings.invoice_prefix or "GK").replace("INV", "WEB")
    except Exception:
        prefix = "GK"
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"


def web_status(pos_status: str | None) -> str:
    return POS_TO_WEB_STATUS.get(pos_status or "pending", pos_status or "pending")


def pos_status(value: str | None) -> str:
    raw = (value or "pending").strip().lower()
    if raw not in WEB_TO_POS_STATUS:
        raise EcommerceSyncError(
            "Invalid status. Use pending, confirmed, packed, shipped, delivered, or cancelled."
        )
    return WEB_TO_POS_STATUS[raw]


def payment_method(value: str | None) -> str:
    raw = (value or "cod").strip().lower()
    if raw not in PAYMENT_METHODS:
        raise EcommerceSyncError(f"Unsupported payment method: {raw}")
    return raw


def payment_status(value: str | None) -> str:
    raw = (value or "pending").strip().lower()
    if raw not in PAYMENT_STATUSES:
        raise EcommerceSyncError(f"Unsupported payment status: {raw}")
    return raw


def expire_old_reservations(now: datetime | None = None) -> int:
    now = now or utcnow()
    rows = db.session.execute(
        db.select(StockReservation).where(
            StockReservation.status == "active",
            StockReservation.expires_at <= now,
        )
    ).scalars().all()
    for row in rows:
        row.status = "expired"
    if rows:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
    return len(rows)


def reserved_quantity(product_id: int, now: datetime | None = None) -> int:
    now = now or utcnow()
    return int(
        db.session.execute(
            db.select(func.coalesce(func.sum(StockReservation.quantity), 0)).where(
                StockReservation.product_id == product_id,
                StockReservation.status == "active",
                StockReservation.expires_at > now,
            )
        ).scalar()
        or 0
    )


def available_quantity(product: Product, now: datetime | None = None) -> int:
    return max(0, int(product.quantity or 0) - reserved_quantity(product.id, now=now))


def _product_for_item(item: dict[str, Any]) -> Product:
    product_id = item.get("product_id")
    sku = (item.get("sku") or "").strip()
    if product_id:
        product = db.session.get(Product, int(product_id))
    elif sku:
        product = db.session.execute(db.select(Product).where(Product.sku == sku)).scalar_one_or_none()
    else:
        raise EcommerceSyncError("Each item must include product_id or sku.")
    if product is None:
        raise EcommerceSyncError("Product not found.", status_code=404, details={"item": item})
    if getattr(product, "is_active", True) is False:
        raise EcommerceSyncError("Product is inactive.", status_code=409, details={"product_id": product.id})
    return product


def _parse_items(raw_items: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list) or not raw_items:
        raise EcommerceSyncError("items must be a non-empty list.")
    parsed: list[dict[str, Any]] = []
    now = utcnow()
    expire_old_reservations(now)
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise EcommerceSyncError("Each item must be an object.")
        product = _product_for_item(raw)
        try:
            qty = int(raw.get("quantity", 0))
        except (TypeError, ValueError):
            raise EcommerceSyncError("Item quantity must be a number.")
        if qty <= 0:
            raise EcommerceSyncError("Item quantity must be greater than zero.")
        available = available_quantity(product, now=now)
        if available < qty:
            raise EcommerceSyncError(
                "Insufficient stock for product.",
                status_code=409,
                details={
                    "product_id": product.id,
                    "sku": product.sku,
                    "requested_quantity": qty,
                    "available_quantity": available,
                },
            )
        unit_price = money(raw.get("unit_price", product.selling_price))
        parsed.append(
            {
                "product": product,
                "quantity": qty,
                "unit_price": unit_price,
                "subtotal": (unit_price * qty).quantize(Decimal("0.01")),
            }
        )
    return parsed


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, default=str, sort_keys=True)


def log_sync(
    *,
    direction: str,
    entity_type: str,
    action: str,
    status: str,
    entity_id: str | None = None,
    idempotency_key: str | None = None,
    request_payload: Any = None,
    response_payload: Any = None,
    error_message: str | None = None,
) -> SyncLog:
    row = SyncLog(
        direction=direction,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        status=status,
        idempotency_key=idempotency_key,
        request_payload=_json_dumps(request_payload) if request_payload is not None else None,
        response_payload=_json_dumps(response_payload) if response_payload is not None else None,
        error_message=error_message,
    )
    db.session.add(row)
    return row


def existing_idempotent_response(idempotency_key: str | None) -> dict[str, Any] | None:
    if not idempotency_key:
        return None
    row = db.session.execute(
        db.select(SyncLog).where(
            SyncLog.idempotency_key == idempotency_key,
            SyncLog.status == "success",
        )
    ).scalar_one_or_none()
    if row and row.response_payload:
        return json.loads(row.response_payload)
    return None


def order_to_dict(order: OnlineOrder) -> dict[str, Any]:
    payment_rows = list(getattr(order, "payments", []) or [])
    latest_payment = payment_rows[-1] if payment_rows else None
    return {
        "id": order.id,
        "order_number": order.order_number,
        "status": web_status(order.status),
        "pos_status": order.status,
        "customer": {
            "name": order.customer_name,
            "phone": order.customer_phone,
            "email": order.customer_email,
            "address": order.delivery_address,
            "area": order.delivery_area,
        },
        "items": [
            {
                "product_id": item.product_id,
                "name": item.product_name,
                "quantity": item.quantity,
                "unit_price": float(item.unit_price),
                "subtotal": float(item.subtotal),
            }
            for item in order.items
        ],
        "amounts": {
            "subtotal": float(order.total_amount),
            "delivery_charge": float(order.delivery_charge or 0),
            "discount": float(order.discount_amount or 0),
            "grand_total": float(order.grand_total),
            "currency": "NPR",
        },
        "payment": {
            "method": order.payment_mode,
            "status": order.payment_status,
            "provider": latest_payment.provider if latest_payment else order.payment_mode,
            "transaction_id": latest_payment.transaction_id if latest_payment else None,
        },
        "order_source": order.order_source,
        "assigned_to": order.assigned_to,
        "estimated_delivery": order.estimated_delivery.isoformat() if order.estimated_delivery else None,
        "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
        "cancelled_at": order.cancelled_at.isoformat() if order.cancelled_at else None,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "updated_at": order.updated_at.isoformat() if order.updated_at else None,
    }


def product_to_dict(product: Product) -> dict[str, Any]:
    available = available_quantity(product)
    reserved = reserved_quantity(product.id)
    return {
        "id": product.id,
        "sku": product.sku,
        "barcode": product.barcode,
        "name": product.name,
        "category": product.category,
        "unit": product.unit or "pcs",
        "description": product.description or "",
        "pack_size": product.pack_size or "",
        "is_featured": bool(getattr(product, 'is_featured', False)),
        "price": float(product.selling_price),
        "stock_quantity": int(product.quantity or 0),
        "reserved_quantity": reserved,
        "available_quantity": available,
        "low_stock_threshold": int(product.low_stock_threshold or 0),
        "is_low_stock": available <= int(product.low_stock_threshold or 0),
        "is_active": bool(getattr(product, "is_active", True)),
        "image_filename": product.image_filename,
        "updated_at": product.updated_at.isoformat() if product.updated_at else None,
    }


def create_order(payload: dict[str, Any], idempotency_key: str | None = None) -> tuple[dict[str, Any], bool]:
    existing = existing_idempotent_response(idempotency_key)
    if existing:
        return existing, True

    customer = payload.get("customer") or {}
    if not isinstance(customer, dict):
        raise EcommerceSyncError("customer must be an object.")
    name = (customer.get("name") or payload.get("customer_name") or "").strip()
    phone = (customer.get("phone") or payload.get("customer_phone") or "").strip()
    address = (customer.get("address") or payload.get("delivery_address") or "").strip()
    if not name or not phone or not address:
        raise EcommerceSyncError("customer.name, customer.phone, and customer.address are required.")

    items = _parse_items(payload.get("items"))
    subtotal = sum((item["subtotal"] for item in items), Decimal("0.00"))
    delivery_charge = money(payload.get("delivery_charge", 0))
    discount = money(payload.get("discount_amount", payload.get("discount", 0)))
    grand_total = (subtotal + delivery_charge - discount).quantize(Decimal("0.01"))
    if grand_total < 0:
        raise EcommerceSyncError("Grand total cannot be negative.")

    payment = payload.get("payment") or {}
    if not isinstance(payment, dict):
        raise EcommerceSyncError("payment must be an object.")
    method = payment_method(payment.get("method") or payload.get("payment_method"))
    pay_status = payment_status(payment.get("status") or payload.get("payment_status"))
    order_status = pos_status(payload.get("status") or "pending")
    reservation_minutes = clamp_reservation_minutes(payload.get("reservation_minutes"))
    expires_at = utcnow() + timedelta(minutes=reservation_minutes)
    external_order_id = (payload.get("external_order_id") or "").strip()

    notes = (payload.get("notes") or "").strip()
    if external_order_id:
        notes = f"{notes}\nExternal order id: {external_order_id}".strip()
    if idempotency_key:
        notes = f"{notes}\nIdempotency key: {idempotency_key}".strip()

    order = OnlineOrder(
        order_number=payload.get("order_number") or generate_order_number(),
        customer_name=name,
        customer_phone=phone,
        customer_email=(customer.get("email") or payload.get("customer_email") or "").strip() or None,
        delivery_address=address,
        delivery_area=(customer.get("area") or payload.get("delivery_area") or "").strip() or None,
        total_amount=subtotal,
        delivery_charge=delivery_charge,
        discount_amount=discount,
        payment_mode=method,
        payment_status=pay_status,
        status=order_status,
        notes=notes or None,
        assigned_to=(payload.get("assigned_to") or "").strip() or None,
        order_source=payload.get("order_source") or "website",
    )
    db.session.add(order)
    db.session.flush()  # get order.id before adding items

    Customer.upsert(name=name, phone=phone, address=address)

    for idx, item in enumerate(items, start=1):
        product = item["product"]

        # Re-check availability inside the transaction with a row-level lock
        # (SELECT FOR UPDATE) to prevent two concurrent checkouts from both
        # reading "1 in stock" and both succeeding — resulting in -1 stock.
        from ..models.product import Product as _ProductModel
        locked_product = db.session.execute(
            db.select(_ProductModel)
            .where(_ProductModel.id == product.id)
            .with_for_update()
        ).scalar_one_or_none()

        if locked_product is None:
            raise EcommerceSyncError(
                f"Product '{product.name}' no longer exists.",
                status_code=409,
            )

        now_locked = utcnow()
        available_now = available_quantity(locked_product, now=now_locked)
        if available_now < item["quantity"]:
            raise EcommerceSyncError(
                f"Insufficient stock for '{locked_product.name}'. "
                f"Only {available_now} available.",
                status_code=409,
                details={
                    "product_id": locked_product.id,
                    "sku": locked_product.sku,
                    "requested_quantity": item["quantity"],
                    "available_quantity": available_now,
                },
            )

        db.session.add(
            OnlineOrderItem(
                order_id=order.id,
                product_id=product.id,
                product_name=product.name,
                quantity=item["quantity"],
                unit_price=item["unit_price"],
                subtotal=item["subtotal"],
            )
        )
        db.session.add(
            StockReservation(
                reservation_key=f"{order.order_number}:{product.id}:{idx}",
                order_id=order.id,
                product_id=product.id,
                quantity=item["quantity"],
                status="active",
                source="website",
                expires_at=expires_at,
            )
        )

    provider = (payment.get("provider") or method).strip().lower()
    db.session.add(
        EcommercePayment(
            order_id=order.id,
            provider=provider,
            method=method,
            amount=grand_total,
            status=pay_status,
            transaction_id=(payment.get("transaction_id") or "").strip() or None,
            gateway_reference=(payment.get("gateway_reference") or "").strip() or None,
            raw_payload_json=_json_dumps(payment) if payment else None,
        )
    )

    response = {
        "ok": True,
        "duplicate": False,
        "reservation_expires_at": expires_at.isoformat(),
        "order": order_to_dict(order),
    }
    log_sync(
        direction="website_to_pos",
        entity_type="online_order",
        entity_id=str(order.id),
        action="create",
        status="success",
        idempotency_key=idempotency_key,
        request_payload=payload,
        response_payload=response,
    )
    db.session.commit()

    # ── Send order confirmation email to customer ─────────────────────────
    # Fires after commit so the order is safely persisted first.
    # Failures are caught and logged — never block the order response.
    try:
        from .email_service import send_order_confirmation, send_admin_new_order_notification
        from ..models.shop_settings import ShopSettings
        order_items = [
            {
                "name": it.product_name,
                "qty": it.quantity,
                "unit_price": float(it.unit_price),
                "subtotal": float(it.subtotal),
            }
            for it in order.items
        ]
        send_order_confirmation(order, order_items)

        # ── Notify admin of new order ─────────────────────────────────────
        settings = ShopSettings.get()
        admin_email = getattr(settings, "email", None) or getattr(settings, "contact_email", None)
        if admin_email:
            send_admin_new_order_notification(order, admin_email)

        # ── Admin SMS notification (fallback if no email) ─────────────────
        elif not admin_email:
            admin_phone = getattr(settings, "phone", None) or getattr(settings, "contact_phone", None)
            if admin_phone:
                from .notification_service import send_notification
                msg = (
                    f"[GoldKernel] New order {order.order_number}. "
                    f"Amount: NPR {float(order.grand_total):.0f}. "
                    f"{order.customer_name} ({order.customer_phone})."
                )
                send_notification(admin_phone, msg)
    except Exception as _email_exc:
        import logging as _log
        _log.getLogger(__name__).warning(
            "Post-order notifications failed for %s: %s",
            order.order_number, _email_exc
        )

    return response, False


def _consume_reservations(order: OnlineOrder) -> None:
    active_rows = [
        row for row in getattr(order, "stock_reservations", [])
        if row.status == "active"
    ]
    if not active_rows:
        return
    for row in active_rows:
        product = db.session.get(Product, row.product_id)
        if product is None:
            raise EcommerceSyncError(
                "Reserved product no longer exists.",
                status_code=409,
                details={"product_id": row.product_id},
            )
        if int(product.quantity or 0) < int(row.quantity or 0):
            raise EcommerceSyncError(
                "POS stock changed after reservation; order needs manual review.",
                status_code=409,
                details={
                    "product_id": product.id,
                    "sku": product.sku,
                    "reserved_quantity": row.quantity,
                    "current_stock": product.quantity,
                },
            )
    for row in active_rows:
        product = db.session.get(Product, row.product_id)
        product.quantity = int(product.quantity or 0) - int(row.quantity or 0)
        row.status = "consumed"
        row.updated_at = utcnow()


def _release_or_restore_stock(order: OnlineOrder) -> None:
    reservations = list(getattr(order, "stock_reservations", []) or [])
    # Website orders: reservations handle stock — never fall through to item-based restore
    # Only POS-created orders (no reservations) use item-based restore
    if reservations:
        for row in reservations:
            product = db.session.get(Product, row.product_id)
            # Safety guard: skip rows that are already released/expired
            if row.status in {"released", "expired"}:
                continue
            if row.status == "consumed" and product:
                product.quantity = int(product.quantity or 0) + int(row.quantity or 0)
            if row.status in {"active", "consumed"}:
                row.status = "released"
                row.updated_at = utcnow()
        return

    # POS-created only — no reservations exist
    for item in order.items:
        product = db.session.get(Product, item.product_id)
        if product:
            product.quantity = int(product.quantity or 0) + int(item.quantity or 0)


def apply_order_status(
    order: OnlineOrder,
    new_pos_status: str,
    note: str | None = None,
    actor: str = "api",
) -> OnlineOrder:
    old_status = order.status
    if new_pos_status == "cancelled" and old_status != "cancelled":
        _release_or_restore_stock(order)
        order.cancelled_at = utcnow()
        order.cancel_reason = note or f"Cancelled by {actor}"
    elif new_pos_status in CONFIRMED_POS_STATUSES:
        _consume_reservations(order)

    order.status = new_pos_status
    if new_pos_status == "delivered":
        order.delivered_at = utcnow()
        if order.payment_mode != "cod":
            order.payment_status = "paid"
    if note:
        order.notes = (order.notes or "") + f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}] {note}"
    return order


def get_order(order_id: Any = None, order_number: str | None = None) -> OnlineOrder:
    if order_id:
        order = db.session.get(OnlineOrder, int(order_id))
    elif order_number:
        order = db.session.execute(
            db.select(OnlineOrder).where(OnlineOrder.order_number == order_number)
        ).scalar_one_or_none()
    else:
        raise EcommerceSyncError("order_id or order_number is required.")
    if order is None:
        raise EcommerceSyncError("Order not found.", status_code=404)
    return order


def update_order_status(payload: dict[str, Any]) -> dict[str, Any]:
    order = get_order(payload.get("order_id"), payload.get("order_number"))
    new_pos_status = pos_status(payload.get("status"))
    note = (payload.get("note") or "").strip() or None
    apply_order_status(order, new_pos_status, note=note, actor=payload.get("actor") or "api")
    response = {"ok": True, "order": order_to_dict(order)}
    log_sync(
        direction="pos_to_website",
        entity_type="online_order",
        entity_id=str(order.id),
        action="update_status",
        status="success",
        request_payload=payload,
        response_payload=response,
    )
    db.session.commit()
    return response


def list_orders(status: str | None = None, order_number: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    stmt = db.select(OnlineOrder).order_by(OnlineOrder.created_at.desc())
    if status:
        stmt = stmt.where(OnlineOrder.status == pos_status(status))
    if order_number:
        stmt = stmt.where(OnlineOrder.order_number == order_number)
    limit = max(1, min(int(limit or 100), 500))
    return [order_to_dict(order) for order in db.session.execute(stmt.limit(limit)).scalars().all()]


def list_products(q: str | None = None, category: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    expire_old_reservations()
    stmt = db.select(Product).where(Product.is_active.isnot(False)).order_by(Product.name)
    if q:
        term = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            db.or_(
                func.lower(Product.name).like(term),
                func.lower(Product.sku).like(term),
                func.lower(func.coalesce(Product.category, "")).like(term),
            )
        )
    if category:
        stmt = stmt.where(func.lower(func.coalesce(Product.category, "")) == category.strip().lower())
    limit = max(1, min(int(limit or 200), 500))
    return [product_to_dict(product) for product in db.session.execute(stmt.limit(limit)).scalars().all()]


def inventory_snapshot(limit: int = 500) -> dict[str, Any]:
    expire_old_reservations()
    stmt = db.select(Product).where(Product.is_active.isnot(False)).order_by(Product.name)
    limit = max(1, min(int(limit or 500), 1000))
    products = db.session.execute(stmt.limit(limit)).scalars().all()
    rows = [product_to_dict(product) for product in products]
    return {
        "ok": True,
        "source": "pos",
        "synced_at": utcnow().isoformat(),
        "inventory": rows,
    }


def sync_inventory(payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("items")
    updated = 0
    if items is not None:
        if not isinstance(items, list):
            raise EcommerceSyncError("items must be a list.")
        for item in items:
            product = _product_for_item(item)
            if "quantity" in item:
                product.quantity = int(item["quantity"])
            if "selling_price" in item:
                product.selling_price = money(item["selling_price"])
            if "is_active" in item:
                product.is_active = bool(item["is_active"])
            updated += 1
    response = inventory_snapshot()
    response["updated"] = updated
    log_sync(
        direction="pos_to_website",
        entity_type="inventory",
        action="sync",
        status="success",
        request_payload=payload,
        response_payload={"updated": updated},
    )
    db.session.commit()
    return response
