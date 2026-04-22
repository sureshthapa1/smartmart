from __future__ import annotations

import csv
import io
from datetime import date

from flask import Blueprint, Response, jsonify, render_template, request

from ...extensions import db
from ...services.decorators import login_required, permission_required
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


def _require_bi_perm(perm: str = "can_view_reports") -> None:
    """Abort 403 if staff user lacks the given permission. Admins always pass."""
    from flask import abort
    from flask_login import current_user
    if current_user.role != "admin":
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(current_user.id)
        if not getattr(p, perm, False):
            abort(403)


# ── Batch endpoints ───────────────────────────────────────────────────────────

@bi_bp.route("/batch", methods=["POST"])
@login_required
def create_batch():
    _require_bi_perm("can_view_purchases")
    data = request.get_json() or {}
    batch = BatchService.create_batch(
        purchase_date=_parse_date(data.get("purchase_date")) or date.today(),
        supplier_name=data.get("supplier_name"),
        allocation_method=data.get("allocation_method") or "value",
        items=data.get("items") or [],
        expenses=data.get("expenses") or [],
    )
    return jsonify(_serialize_batch(batch)), 201


@bi_bp.route("/batch", methods=["GET"])
@login_required
def list_batches():
    _require_bi_perm("can_view_purchases")
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(100, max(1, int(request.args.get("per_page", 20))))
    status = request.args.get("status")
    stmt = db.select(PurchaseBatch).order_by(PurchaseBatch.purchase_date.desc(), PurchaseBatch.id.desc())
    if status:
        stmt = stmt.where(PurchaseBatch.status == status)
    total = db.session.execute(db.select(db.func.count()).select_from(stmt.subquery())).scalar() or 0
    batches = db.session.execute(stmt.offset((page - 1) * per_page).limit(per_page)).scalars().all()
    return jsonify({
        "batches": [_serialize_batch(b) for b in batches],
        "total": total,
        "page": page,
        "per_page": per_page,
    })


@bi_bp.route("/batch/<int:batch_id>", methods=["GET"])
@login_required
def get_batch(batch_id: int):
    _require_bi_perm("can_view_purchases")
    batch = db.session.get(PurchaseBatch, batch_id)
    if batch is None:
        return jsonify({"error": "batch not found"}), 404
    return jsonify(_serialize_batch(batch))


@bi_bp.route("/batch/<int:batch_id>", methods=["DELETE"])
@login_required
def delete_batch(batch_id: int):
    _require_bi_perm("can_create_purchase")
    batch = db.session.get(PurchaseBatch, batch_id)
    if batch is None:
        return jsonify({"error": "batch not found"}), 404
    if batch.status != "draft":
        return jsonify({"error": "Only draft batches can be deleted"}), 400
    db.session.delete(batch)
    db.session.commit()
    return jsonify({"deleted": batch_id})


@bi_bp.route("/batch/<int:batch_id>/items", methods=["POST"])
@login_required
def add_batch_items(batch_id: int):
    _require_bi_perm("can_create_purchase")
    batch = BatchService.add_items(batch_id, (request.get_json() or {}).get("items") or [])
    return jsonify(_serialize_batch(batch))


# Feature 2: remove a single item from a draft batch
@bi_bp.route("/batch/<int:batch_id>/items/<int:item_id>", methods=["DELETE"])
@login_required
def remove_batch_item(batch_id: int, item_id: int):
    _require_bi_perm("can_create_purchase")
    batch = BatchService.remove_item(batch_id, item_id)
    return jsonify(_serialize_batch(batch))


@bi_bp.route("/batch/<int:batch_id>/expenses", methods=["POST"])
@login_required
def add_batch_expenses(batch_id: int):
    _require_bi_perm("can_create_purchase")
    batch = BatchService.add_expenses(batch_id, (request.get_json() or {}).get("expenses") or [])
    return jsonify(_serialize_batch(batch))


# Feature 2: remove a batch expense
@bi_bp.route("/batch/<int:batch_id>/expenses/<int:expense_id>", methods=["DELETE"])
@login_required
def remove_batch_expense(batch_id: int, expense_id: int):
    _require_bi_perm("can_create_purchase")
    batch = BatchService.remove_expense(batch_id, expense_id)
    return jsonify(_serialize_batch(batch))


@bi_bp.route("/batch/<int:batch_id>/recalculate", methods=["POST"])
@login_required
def recalculate_batch(batch_id: int):
    _require_bi_perm("can_create_purchase")
    batch = BatchService.recalculate_allocation(batch_id)
    db.session.commit()
    return jsonify(_serialize_batch(batch))


@bi_bp.route("/batch/<int:batch_id>/finalize", methods=["POST"])
@login_required
def finalize_batch(batch_id: int):
    _require_bi_perm("can_create_purchase")
    batch = BatchService.finalize_batch(batch_id)
    return jsonify(_serialize_batch(batch))


# ── Product endpoints ─────────────────────────────────────────────────────────

@bi_bp.route("/products", methods=["POST"])
@login_required
def upsert_product():
    _require_bi_perm("can_add_product")
    product = ProductService.upsert_product(request.get_json() or {})
    return jsonify({"id": product.id, "sku": product.sku}), 201


@bi_bp.route("/products", methods=["GET"])
@login_required
def list_products():
    _require_bi_perm("can_view_inventory")
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(200, max(1, int(request.args.get("per_page", 50))))
    search = (request.args.get("q") or "").strip().lower()
    all_products = ProductService.list_products()
    if search:
        all_products = [p for p in all_products if search in p["name"].lower() or search in (p["sku"] or "").lower()]
    total = len(all_products)
    start = (page - 1) * per_page
    return jsonify({
        "products": all_products[start: start + per_page],
        "total": total,
        "page": page,
        "per_page": per_page,
    })


@bi_bp.route("/products/pricing/rules", methods=["POST"])
@login_required
def upsert_margin_rule():
    _require_bi_perm("can_edit_product")
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


# Feature 3: list all margin rules
@bi_bp.route("/products/pricing/rules", methods=["GET"])
@login_required
def list_margin_rules():
    _require_bi_perm("can_view_inventory")
    return jsonify({"rules": PricingService.list_margin_rules()})


@bi_bp.route("/products/pricing/suggest", methods=["POST"])
@login_required
def suggest_price():
    _require_bi_perm("can_view_inventory")
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


# ── Sales endpoints ───────────────────────────────────────────────────────────

@bi_bp.route("/sales", methods=["POST"])
@login_required
def create_sale():
    _require_bi_perm("can_create_sale")
    payload = request.get_json() or {}
    sale = SalesService.create_sale(payload, user_id=payload.get("user_id"))
    return jsonify(SalesService.serialize_sale(sale)), 201


# ── Expense endpoints ─────────────────────────────────────────────────────────

@bi_bp.route("/expenses", methods=["POST"])
@login_required
def create_expense():
    _require_bi_perm("can_manage_expenses")
    expense = ExpenseService.create_opex(request.get_json() or {})
    return jsonify({
        "id": expense.id,
        "amount": decimal_to_float(expense.amount),
        "category": expense.category,
        "date": expense.expense_date.isoformat(),
        "payment_method": expense.payment_method,
    }), 201


@bi_bp.route("/expenses", methods=["GET"])
@login_required
def list_expenses():
    _require_bi_perm("can_view_expenses")
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(200, max(1, int(request.args.get("per_page", 50))))
    all_expenses = ExpenseService.list_opex(
        start=_parse_date(request.args.get("start")),
        end=_parse_date(request.args.get("end")),
    )
    total = len(all_expenses)
    start_idx = (page - 1) * per_page
    return jsonify({
        "expenses": all_expenses[start_idx: start_idx + per_page],
        "total": total,
        "page": page,
        "per_page": per_page,
    })


@bi_bp.route("/expenses/<int:expense_id>", methods=["DELETE"])
@login_required
def delete_expense(expense_id: int):
    _require_bi_perm("can_manage_expenses")
    from ..models.operating_expense import OperatingExpense
    expense = db.session.get(OperatingExpense, expense_id)
    if expense is None:
        return jsonify({"error": "expense not found"}), 404
    db.session.delete(expense)
    db.session.commit()
    return jsonify({"deleted": expense_id})


# FIX 4: expense update endpoint
@bi_bp.route("/expenses/<int:expense_id>", methods=["PATCH"])
@login_required
def update_expense(expense_id: int):
    _require_bi_perm("can_manage_expenses")
    expense = ExpenseService.update_opex(expense_id, request.get_json() or {})
    return jsonify({
        "id": expense.id,
        "amount": decimal_to_float(expense.amount),
        "category": expense.category,
        "date": expense.expense_date.isoformat(),
        "payment_method": expense.payment_method,
        "note": expense.note,
    })


# ── Report endpoints ──────────────────────────────────────────────────────────

@bi_bp.route("/reports/profit-loss", methods=["GET"])
@login_required
def report_profit_loss():
    _require_bi_perm("can_view_profit_report")
    start = _parse_date(request.args.get("start"))
    end = _parse_date(request.args.get("end"))
    return jsonify(ReportService.profit_and_loss(start, end))


@bi_bp.route("/reports/profit-loss/export-csv", methods=["GET"])
@login_required
def export_profit_loss_csv():
    _require_bi_perm("can_view_profit_report")
    start = _parse_date(request.args.get("start"))
    end = _parse_date(request.args.get("end"))
    data = ReportService.profit_and_loss(start, end)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Product ID", "Product Name", "SKU", "Cost", "Selling Price",
        "Qty Sold", "Revenue", "COGS", "Gross Profit", "Gross Margin %",
        "Allocated OpEx", "Net Profit", "Net Margin %", "Break-Even Price",
    ])
    for row in data.get("products", []):
        writer.writerow([
            row["product_id"], row.get("product_name", ""), row.get("sku", ""),
            row.get("cost", ""), row.get("selling_price", ""),
            row.get("qty_sold", ""), row["revenue"], row["cogs"],
            row["gross_profit"], row.get("gross_margin_pct", ""),
            row["allocated_opex"], row["net_profit"],
            row.get("net_margin_pct", ""), row.get("break_even_price", ""),
        ])
    overall = data.get("overall", {})
    writer.writerow([])
    writer.writerow([
        "TOTAL", "", "", "", "", "",
        overall.get("sales"), overall.get("cogs"),
        overall.get("gross_profit"), overall.get("gross_margin_pct", ""),
        overall.get("opex"), overall.get("net_profit"),
        overall.get("net_margin_pct", ""), "",
    ])
    filename = f"pnl_{(start or date.today()).isoformat()}_{(end or date.today()).isoformat()}.csv"
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


@bi_bp.route("/reports/dashboard", methods=["GET"])
@login_required
def dashboard_data():
    _require_bi_perm("can_view_reports")
    filter_key = (request.args.get("filter") or "today").lower()
    start = _parse_date(request.args.get("start"))
    end = _parse_date(request.args.get("end"))
    return jsonify(DashboardService.payload(filter_key, start, end))


# Feature 1: reorder alerts
@bi_bp.route("/reports/reorder-alerts", methods=["GET"])
@login_required
def reorder_alerts():
    _require_bi_perm("can_view_reports")
    return jsonify({"alerts": ReportService.reorder_alerts()})


# Feature 4: category-level P&L
@bi_bp.route("/reports/profit-loss/by-category", methods=["GET"])
@login_required
def report_profit_loss_by_category():
    _require_bi_perm("can_view_profit_report")
    start = _parse_date(request.args.get("start"))
    end = _parse_date(request.args.get("end"))
    return jsonify(ReportService.profit_and_loss_by_category(start, end))


# Feature 9: pricing rules delete
@bi_bp.route("/products/pricing/rules/<category>", methods=["DELETE"])
@login_required
def delete_margin_rule(category: str):
    _require_bi_perm("can_edit_product")
    from ..models.pricing import CategoryMarginRule
    rule = db.session.execute(
        db.select(CategoryMarginRule).where(
            CategoryMarginRule.category == category.strip().lower()
        )
    ).scalar_one_or_none()
    if rule is None:
        return jsonify({"error": "rule not found"}), 404
    db.session.delete(rule)
    db.session.commit()
    return jsonify({"deleted": category})


# Feature 10: inventory valuation snapshot
@bi_bp.route("/reports/inventory-value", methods=["GET"])
@login_required
def inventory_value_snapshot():
    _require_bi_perm("can_view_reports")
    return jsonify(ReportService.inventory_valuation_snapshot())


# ── Break-even price endpoint ─────────────────────────────────────────────────
@bi_bp.route("/reports/break-even", methods=["GET"])
@login_required
def break_even_report():
    """
    Returns break-even price for every product that has been sold.
    break_even_price = (cogs + allocated_opex) / qty_sold
    """
    _require_bi_perm("can_view_profit_report")
    start = _parse_date(request.args.get("start"))
    end = _parse_date(request.args.get("end"))
    data = ReportService.profit_and_loss(start, end)
    result = [
        {
            "product_id": p["product_id"],
            "product_name": p["product_name"],
            "sku": p["sku"],
            "cost": p["cost"],
            "selling_price": p["selling_price"],
            "qty_sold": p["qty_sold"],
            "break_even_price": p["break_even_price"],
            "current_vs_breakeven": round(p["selling_price"] - p["break_even_price"], 2),
            "is_above_breakeven": p["selling_price"] >= p["break_even_price"],
        }
        for p in data["products"]
    ]
    # Sort: products below break-even first
    result.sort(key=lambda x: x["current_vs_breakeven"])
    return jsonify({"break_even": result, "overall": data["overall"]})


# ── Profit simulation endpoint ────────────────────────────────────────────────
@bi_bp.route("/reports/simulate", methods=["POST"])
@login_required
def simulate_profit():
    """
    What-if profit simulation.
    POST body: { product_id, margin_pct OR selling_price, expected_qty, start?, end? }
    """
    _require_bi_perm("can_view_profit_report")
    payload = request.get_json() or {}
    product_id = payload.get("product_id")
    if not product_id:
        return jsonify({"error": "product_id is required"}), 400
    return jsonify(
        ReportService.simulate_profit(
            product_id=int(product_id),
            margin_pct=payload.get("margin_pct"),
            selling_price=payload.get("selling_price"),
            expected_qty=int(payload.get("expected_qty") or 1),
            start=_parse_date(payload.get("start")),
            end=_parse_date(payload.get("end")),
        )
    )


# Feature 7: batch CSV export
@bi_bp.route("/batch/<int:batch_id>/export-csv", methods=["GET"])
@login_required
def export_batch_csv(batch_id: int):
    _require_bi_perm("can_view_purchases")
    batch = db.session.get(PurchaseBatch, batch_id)
    if batch is None:
        return jsonify({"error": "batch not found"}), 404
    csv_data = ReportService.batch_to_csv(batch)
    filename = f"batch_{batch_id}_{batch.purchase_date.isoformat()}.csv"
    return Response(csv_data, mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


# Inventory ledger read endpoint
@bi_bp.route("/inventory/ledger", methods=["GET"])
@login_required
def inventory_ledger():
    _require_bi_perm("can_view_reports")
    limit = min(500, max(1, int(request.args.get("limit", 100))))
    offset = max(0, int(request.args.get("offset", 0)))
    product_id = request.args.get("product_id", type=int)
    movement_type = request.args.get("movement_type")
    return jsonify(ExpenseService.list_ledger(
        product_id=product_id,
        movement_type=movement_type,
        limit=limit,
        offset=offset,
    ))


# ── AI Advisor endpoint ───────────────────────────────────────────────────────

@bi_bp.route("/ai/advisor", methods=["GET"])
@login_required
def ai_advisor():
    _require_bi_perm("can_view_ai_insights")
    return jsonify(
        AIAdvisorService.analyze(
            low_margin_threshold=float(request.args.get("low_margin_threshold", 0.10)),
            dead_stock_days=int(request.args.get("dead_stock_days", 30)),
            overstock_qty_threshold=int(request.args.get("overstock_qty_threshold", 100)),
            low_movement_days=int(request.args.get("low_movement_days", 30)),
            low_movement_sales_qty=int(request.args.get("low_movement_sales_qty", 5)),
        )
    )


# ── Dashboard UI ──────────────────────────────────────────────────────────────

@bi_dashboard_bp.route("/dashboard", methods=["GET"])
@login_required
def bi_dashboard_page():
    _require_bi_perm("can_view_reports")
    return render_template("bi/dashboard.html")


@bi_dashboard_bp.route("/batches", methods=["GET"])
@login_required
def bi_batches_page():
    """Task 10: BI Batch UI page."""
    _require_bi_perm("can_view_purchases")
    return render_template("bi/batches.html")


# ── Error handlers ────────────────────────────────────────────────────────────

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
