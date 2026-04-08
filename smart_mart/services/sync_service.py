from __future__ import annotations

import json
from datetime import datetime, timezone

from ..extensions import db
from ..models.ai_enhancements import DeviceSyncState, SyncEvent
from ..models.customer import Customer
from ..models.product import Product
from .ai_decision_logger import log_decision


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _upsert_customer(payload: dict) -> tuple[str, int]:
    name = (payload.get("name") or "").strip()
    if not name:
        raise ValueError("Customer name is required for sync.")
    customer = db.session.execute(
        db.select(Customer).where(db.func.lower(Customer.name) == name.lower())
    ).scalar_one_or_none()
    if customer is None:
        customer = Customer(name=name, phone=payload.get("phone"), address=payload.get("address"))
        db.session.add(customer)
        db.session.flush()
        return ("created", customer.id)
    customer.phone = payload.get("phone") or customer.phone
    customer.address = payload.get("address") or customer.address
    customer.last_visit = _utcnow()
    return ("updated", customer.id)


def _upsert_product(payload: dict) -> tuple[str, int]:
    sku = (payload.get("sku") or "").strip()
    if not sku:
        raise ValueError("Product SKU is required for sync.")
    product = db.session.execute(db.select(Product).where(Product.sku == sku)).scalar_one_or_none()
    if product is None:
        product = Product(
            name=payload.get("name") or sku,
            category=payload.get("category"),
            sku=sku,
            cost_price=payload.get("cost_price") or 0,
            selling_price=payload.get("selling_price") or 0,
            quantity=payload.get("quantity") or 0,
        )
        db.session.add(product)
        db.session.flush()
        return ("created", product.id)
    product.name = payload.get("name") or product.name
    product.category = payload.get("category") or product.category
    if "quantity" in payload:
        product.quantity = int(payload["quantity"])
    if "selling_price" in payload:
        product.selling_price = payload["selling_price"]
    if "cost_price" in payload:
        product.cost_price = payload["cost_price"]
    return ("updated", product.id)


def push_events(device_id: str, events: list[dict]) -> dict:
    applied = 0
    conflicts = 0
    ignored = 0
    results = []

    for event in events:
        entity_type = event.get("entity_type")
        operation = event.get("operation", "upsert")
        payload = event.get("payload") or {}
        client_ts = _parse_ts(event.get("client_timestamp"))
        status = "applied"
        conflict_reason = None
        entity_id = None

        try:
            if operation != "upsert":
                status = "ignored"
                conflict_reason = "Only upsert operation supported for now."
            elif entity_type == "customer":
                action, entity_id = _upsert_customer(payload)
                if action == "updated" and client_ts and client_ts < (_utcnow().replace(microsecond=0)):
                    pass
            elif entity_type == "product":
                action, entity_id = _upsert_product(payload)
                if action == "updated" and client_ts and client_ts < (_utcnow().replace(microsecond=0)):
                    pass
            else:
                status = "ignored"
                conflict_reason = f"Unsupported entity_type: {entity_type}"
        except ValueError as exc:
            status = "conflict"
            conflict_reason = str(exc)
            conflicts += 1
        except Exception as exc:
            status = "conflict"
            conflict_reason = f"Unhandled sync error: {exc}"
            conflicts += 1

        db.session.add(
            SyncEvent(
                device_id=device_id,
                entity_type=entity_type or "unknown",
                entity_id=str(entity_id) if entity_id is not None else None,
                operation=operation,
                payload_json=json.dumps(payload),
                client_timestamp=client_ts,
                status=status,
                conflict_reason=conflict_reason,
            )
        )

        if status == "applied":
            applied += 1
        elif status == "ignored":
            ignored += 1

        results.append(
            {
                "entity_type": entity_type,
                "operation": operation,
                "status": status,
                "conflict_reason": conflict_reason,
            }
        )

    db.session.commit()
    return {"applied": applied, "conflicts": conflicts, "ignored": ignored, "results": results}


def pull_events(device_id: str, since_event_id: int = 0, limit: int = 200) -> dict:
    stmt = (
        db.select(SyncEvent)
        .where(SyncEvent.id > since_event_id)
        .order_by(SyncEvent.id.asc())
        .limit(max(1, min(limit, 500)))
    )
    events = db.session.execute(stmt).scalars().all()
    max_id = since_event_id
    payloads = []
    for event in events:
        payloads.append(
            {
                "id": event.id,
                "device_id": event.device_id,
                "entity_type": event.entity_type,
                "entity_id": event.entity_id,
                "operation": event.operation,
                "payload": json.loads(event.payload_json or "{}"),
                "client_timestamp": event.client_timestamp.isoformat() if event.client_timestamp else None,
                "server_timestamp": event.server_timestamp.isoformat() if event.server_timestamp else None,
                "status": event.status,
            }
        )
        max_id = max(max_id, event.id)

    state = db.session.execute(
        db.select(DeviceSyncState).where(DeviceSyncState.device_id == device_id)
    ).scalar_one_or_none()
    if state is None:
        state = DeviceSyncState(device_id=device_id)
        db.session.add(state)
    state.last_event_id = max_id
    state.last_sync_at = _utcnow()
    db.session.commit()
    return {"events": payloads, "last_event_id": max_id}


def resolve_conflict(sync_event_id: int, strategy: str) -> dict:
    event = db.get_or_404(SyncEvent, sync_event_id)
    if event.status != "conflict":
        return {"status": event.status, "message": "Event is not in conflict."}

    payload = json.loads(event.payload_json or "{}")
    if strategy == "server_wins":
        event.status = "ignored"
        event.conflict_reason = "Resolved with server_wins strategy."
    elif strategy == "client_wins":
        if event.entity_type == "customer":
            _upsert_customer(payload)
            event.status = "applied"
            event.conflict_reason = None
        elif event.entity_type == "product":
            _upsert_product(payload)
            event.status = "applied"
            event.conflict_reason = None
        else:
            raise ValueError(f"Unsupported entity_type for client_wins: {event.entity_type}")
    else:
        raise ValueError("Invalid strategy. Use 'server_wins' or 'client_wins'.")

    log_decision(
        decision_type="sync_conflict_resolution",
        entity_type=event.entity_type,
        entity_id=event.entity_id,
        input_snapshot={"sync_event_id": event.id, "strategy": strategy},
        output_snapshot={"status": event.status, "conflict_reason": event.conflict_reason},
        confidence=1.0,
    )
    db.session.commit()
    return {"status": event.status, "conflict_reason": event.conflict_reason}
