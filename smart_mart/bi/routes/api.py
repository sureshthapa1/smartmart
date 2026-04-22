from __future__ import annotations

from datetime import date

from flask import Blueprint, jsonify, render_template, request

from ...extensions import db
from ..models.purchase_batch import PurchaseBatch
from ..services import (
    AIAdvisorService,
    BatchService,
    DashboardService,
    ExpenseService,
    PricingService,
    ProductService,
    ReportService,
    SalesService,
)
from ..utils import decimal_to_float


bi_bp = Blueprint("bi", __name__, url_prefix="/api/bi")
bi_dashboard_bp = Blueprint("bi_dashboard", __name__, url_prefix="/bi")


def _parse_date(raw: str | None) -> date | None:
    return date.fromisoformat(raw) if raw else None


@bi_bp.route("/batch", methods=["POST"])
def create_batch():
    data = request.get_json() or {}
    batch = BatchService.create_batch(
        purchase_date=_parse_date(data.get("purchase_date")) or date.today(),
        supplier_name=data.get("supplier_name"),
        allocation_method=data.get("allocation_method") or "value",
        items=data.get("items") or [],
        expenses=data.get("expenses") or [],
    )
    return jsonify(_serialize_batch(batch)), 201


@bi_bp.route("/batch/<int:batch_id>", methods=["GET"])
def get_batch(batch_id: int):
    batch = db.session.get(PurchaseBatch, batch_id)
    if batch is None:
        return jsonify({"error": "batch not found"}), 404
    return jsonify(_serialize_batch(batch))


@bi_bp.route("/batch/<int:batch_id>/items", methods=["POST"])
def add_batch_items(batch_id: int):
    batch = BatchService.add_items(batch_id, (request.get_json() or {}).get("items") or [])
    return jsonify(_serialize_batch(batch))


@bi_bp.route("/batch/<int:batch_id>/expenses", methods=["POST"])
def add_batch_expenses(batch_id: int):
    batch = BatchService.add_expenses(batch_id, (request.get_json() or {}).get("expenses") or [])
    return jsonify(_serialize_batch(batch))


@bi_bp.route("/batch/<int:batch_id>/recalculate", methods=["POST"])
def recalculate_batch(batch_id: int):
    batch = BatchService.recalculate_allocation(batch_id)
    db.session.commit()
    return jsonify(_serialize_batch(batch))


@bi_bp.route("/batch/<int:batch_id>/finalize", methods=["POST"])
def finalize_batch(batch_id: int):
    batch = BatchService.finalize_batch(batch_id)
    return jsonify(_serialize_batch(batch))


@bi_bp.route("/products", methods=["POST"])
def upsert_product():
    product = ProductService.upsert_product(request.get_json() or {})
    return jsonify({"id": product.id, "sku": product.sku}), 201


@bi_bp.route("/products", methods=["GET"])
def list_products():
    return jsonify({"products": ProductService.list_products()})


@bi_bp.route("/products/pricing/rules", methods=["POST"])
def upsert_margin_rule():
    payload = request.get_json() or {}
    rule = PricingService.upsert_margin_rule(
        payload.get("category"),
        payload.get("margin_pct"),
        payload.get("rounding_base") or 1,
    )
    return jsonify({
        "category": rule.category,
        "margin_pct": decimal_to_float(rule.margin_pct),
        "rounding_base": rule.rounding_base,
    })


@bi_bp.route("/products/pricing/suggest", methods=["POST"])
def suggest_price():
    payload = request.get_json() or {}
    return jsonify(
        PricingService.suggest_price(
            product_id=payload.get("product_id"),
            cost=payload.get("cost"),
            category=payload.get("category"),
            margin_pct=payload.get("margin_pct"),
            rounding_base=payload.get("rounding_base"),
        )
    )


@bi_bp.route("/sales", methods=["POST"])
def create_sale():
    payload = request.get_json() or {}
    sale = SalesService.create_sale(payload, user_id=payload.get("user_id"))
    return jsonify(SalesService.serialize_sale(sale)), 201


@bi_bp.route("/expenses", methods=["POST"])
def create_expense():
    expense = ExpenseService.create_opex(request.get_json() or {})
    return jsonify({
        "id": expense.id,
        "amount": decimal_to_float(expense.amount),
        "category": expense.category,
        "date": expense.expense_date.isoformat(),
        "payment_method": expense.payment_method,
    }), 201


@bi_bp.route("/expenses", methods=["GET"])
def list_expenses():
    return jsonify(
        {
            "expenses": ExpenseService.list_opex(
                start=_parse_date(request.args.get("start")),
                end=_parse_date(request.args.get("end")),
            )
        }
    )


@bi_bp.route("/reports/profit-loss", methods=["GET"])
def report_profit_loss():
    start = _parse_date(request.args.get("start"))
    end = _parse_date(request.args.get("end"))
    return jsonify(ReportService.profit_and_loss(start, end))


@bi_bp.route("/reports/dashboard", methods=["GET"])
def dashboard_data():
    filter_key = (request.args.get("filter") or "today").lower()
    start = _parse_date(request.args.get("start"))
    end = _parse_date(request.args.get("end"))
    return jsonify(DashboardService.payload(filter_key, start, end))


@bi_bp.route("/ai/advisor", methods=["GET"])
def ai_advisor():
    return jsonify(
        AIAdvisorService.analyze(
            low_margin_threshold=float(request.args.get("low_margin_threshold", 0.10)),
            dead_stock_days=int(request.args.get("dead_stock_days", 30)),
            overstock_qty_threshold=int(request.args.get("overstock_qty_threshold", 100)),
            low_movement_days=int(request.args.get("low_movement_days", 30)),
            low_movement_sales_qty=int(request.args.get("low_movement_sales_qty", 5)),
        )
    )


@bi_dashboard_bp.route("/dashboard", methods=["GET"])
def bi_dashboard_page():
    return render_template("bi/dashboard.html")


@bi_bp.errorhandler(ValueError)
def handle_value_error(exc: ValueError):
    db.session.rollback()
    return jsonify({"error": str(exc)}), 400


def _serialize_batch(batch: PurchaseBatch) -> dict:
    return {
        "id": batch.id,
        "status": batch.status,
        "purchase_date": batch.purchase_date.isoformat(),
        "supplier_name": batch.supplier_name,
        "allocation_method": batch.allocation_method,
        "subtotal_amount": decimal_to_float(batch.subtotal_amount),
        "shared_expense_total": decimal_to_float(batch.shared_expense_total),
        "grand_total": decimal_to_float(batch.grand_total),
        "allocation_snapshot": batch.allocation_snapshot,
        "finalized_at": batch.finalized_at.isoformat() if batch.finalized_at else None,
        "items": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "quantity": item.quantity,
                "purchase_price": decimal_to_float(item.purchase_price),
                "allocated_total": decimal_to_float(item.allocated_total),
                "allocated_cost_per_unit": decimal_to_float(item.allocated_cost_per_unit),
                "final_cost": decimal_to_float(item.final_cost),
                "allocation_detail": item.allocation_detail,
            }
            for item in batch.items
        ],
        "expenses": [
            {
                "id": ex.id,
                "expense_type": ex.expense_type,
                "amount": decimal_to_float(ex.amount),
            }
            for ex in batch.expenses
        ],
    }
