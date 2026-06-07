"""REST API for GoldKernel website/POS integration."""
from __future__ import annotations

import os
from functools import wraps

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user

from ...extensions import db, limiter
from ...services import ecommerce_sync
from ...services.ecommerce_sync import EcommerceSyncError

ecommerce_api_bp = Blueprint("ecommerce_api", __name__, url_prefix="/api")


def _candidate_keys() -> set[str]:
    return {
        key for key in (
            os.environ.get("ECOMMERCE_API_KEY"),
            os.environ.get("POS_SYNC_API_KEY"),
        )
        if key
    }


def _provided_keys() -> set[str]:
    bearer = request.headers.get("Authorization", "")
    if bearer.lower().startswith("bearer "):
        bearer = bearer[7:].strip()
    else:
        bearer = ""
    return {
        key for key in (
            bearer,
            request.headers.get("X-API-Key"),
            request.headers.get("X-Website-API-Key"),
            request.headers.get("X-POS-API-Key"),
        )
        if key
    }


def _is_admin_session() -> bool:
    try:
        return bool(current_user.is_authenticated and current_user.role == "admin")
    except Exception:
        return False


def _is_authorized() -> bool:
    if _is_admin_session():
        return True
    configured = _candidate_keys()
    if not configured and (current_app.testing or current_app.debug):
        return True
    return bool(configured.intersection(_provided_keys()))


def api_key_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _is_authorized():
            return jsonify({"ok": False, "error": "Unauthorized"}), 401
        return view(*args, **kwargs)
    return wrapped


def _json_payload() -> dict:
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        raise EcommerceSyncError("Request body must be a JSON object.")
    return data


def _handle_error(exc: Exception, payload: dict | None = None, action: str = "api"):
    db.session.rollback()
    status_code = exc.status_code if isinstance(exc, EcommerceSyncError) else 500
    body = {
        "ok": False,
        "error": str(exc),
    }
    if isinstance(exc, EcommerceSyncError) and exc.details:
        body["details"] = exc.details
    try:
        ecommerce_sync.log_sync(
            direction="api",
            entity_type="unknown",
            action=action,
            status="failed",
            request_payload=payload,
            response_payload=body,
            error_message=str(exc),
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
    return jsonify(body), status_code


@ecommerce_api_bp.route("/products", methods=["GET"])
@limiter.limit("120/minute")
def products():
    try:
        rows = ecommerce_sync.list_products(
            q=request.args.get("q"),
            category=request.args.get("category"),
            limit=int(request.args.get("limit", 200) or 200),
        )
        return jsonify({"ok": True, "products": rows})
    except Exception as exc:
        return _handle_error(exc, action="products")


@ecommerce_api_bp.route("/inventory", methods=["GET"])
@api_key_required
@limiter.limit("60/minute")
def inventory():
    try:
        snapshot = ecommerce_sync.inventory_snapshot(limit=int(request.args.get("limit", 500) or 500))
        return jsonify(snapshot)
    except Exception as exc:
        return _handle_error(exc, action="inventory")


@ecommerce_api_bp.route("/orders/create", methods=["POST"])
@api_key_required
@limiter.limit("30/minute")
def create_order():
    payload = None
    try:
        payload = _json_payload()
        idempotency_key = request.headers.get("Idempotency-Key") or payload.get("idempotency_key")
        response, duplicate = ecommerce_sync.create_order(payload, idempotency_key=idempotency_key)
        response = dict(response)
        response["duplicate"] = duplicate
        return jsonify(response), 200 if duplicate else 201
    except Exception as exc:
        return _handle_error(exc, payload=payload, action="create_order")


@ecommerce_api_bp.route("/orders", methods=["GET"])
@api_key_required
@limiter.limit("60/minute")
def orders():
    try:
        rows = ecommerce_sync.list_orders(
            status=request.args.get("status"),
            order_number=request.args.get("order_number"),
            limit=int(request.args.get("limit", 100) or 100),
        )
        return jsonify({"ok": True, "orders": rows})
    except Exception as exc:
        return _handle_error(exc, action="orders")


@ecommerce_api_bp.route("/orders/update-status", methods=["PUT"])
@api_key_required
@limiter.limit("60/minute")
def update_status():
    payload = None
    try:
        payload = _json_payload()
        return jsonify(ecommerce_sync.update_order_status(payload))
    except Exception as exc:
        return _handle_error(exc, payload=payload, action="update_status")


@ecommerce_api_bp.route("/sync-pos-order", methods=["POST"])
@api_key_required
@limiter.limit("30/minute")
def sync_pos_order():
    payload = None
    try:
        payload = _json_payload()
        idempotency_key = request.headers.get("Idempotency-Key") or payload.get("idempotency_key")
        response, duplicate = ecommerce_sync.create_order(payload, idempotency_key=idempotency_key)
        response = dict(response)
        response["duplicate"] = duplicate
        response["sync_target"] = "pos_online_orders"
        return jsonify(response), 200 if duplicate else 201
    except Exception as exc:
        return _handle_error(exc, payload=payload, action="sync_pos_order")


@ecommerce_api_bp.route("/sync-inventory", methods=["POST"])
@api_key_required
@limiter.limit("60/minute")
def sync_inventory():
    payload = None
    try:
        payload = _json_payload()
        return jsonify(ecommerce_sync.sync_inventory(payload))
    except Exception as exc:
        return _handle_error(exc, payload=payload, action="sync_inventory")
