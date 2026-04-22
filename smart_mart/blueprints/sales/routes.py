"""Sales blueprint — POS-style sale creation, listing, detail, and invoice routes."""

from __future__ import annotations

from datetime import date, datetime

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ...extensions import db
from ...models.product import Product
from ...services import sales_manager
from ...services.sales_manager import InsufficientStockError
from ...services.decorators import login_required, admin_required

sales_bp = Blueprint("sales", __name__, url_prefix="/sales")


@sales_bp.route("/")
@login_required
def list_sales():
    if current_user.role != "admin":
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(current_user.id)
        if not p.can_view_sales:
            abort(403)

    start_date = request.args.get("start_date", "").strip() or None
    end_date   = request.args.get("end_date",   "").strip() or None
    search_q   = request.args.get("q",          "").strip() or None
    pay_filter = request.args.get("payment",     "").strip() or None
    page       = int(request.args.get("page", 1))

    filters: dict = {}
    if start_date:
        try:
            filters["start_date"] = datetime.fromisoformat(start_date)
        except ValueError:
            flash("Invalid start date format.", "danger")
    if end_date:
        try:
            filters["end_date"] = datetime.fromisoformat(end_date)
        except ValueError:
            flash("Invalid end date format.", "danger")
    if search_q:
        filters["search"] = search_q
    if pay_filter:
        filters["payment_mode"] = pay_filter

    sales = sales_manager.list_sales(filters, page=page)
    return render_template(
        "sales/list.html",
        sales=sales,
        page=page,
        start_date=start_date or "",
        end_date=end_date or "",
        search_q=search_q or "",
        pay_filter=pay_filter or "",
    )


@sales_bp.route("/create", methods=["GET", "POST"])
@login_required
def create_sale():
    if current_user.role != "admin":
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(current_user.id)
        if not p.can_create_sale:
            abort(403)
    products = db.session.execute(
        db.select(Product).order_by(Product.name)
    ).scalars().all()

    if request.method == "POST":
        items = _parse_items(request.form)
        if not items:
            flash("Please add at least one item to the sale.", "danger")
            return render_template("sales/create.html", products=products)

        try:
            sale = sales_manager.create_sale(
                items,
                user_id=current_user.id,
                customer_name=request.form.get("customer_name", "").strip() or None,
                customer_address=request.form.get("customer_address", "").strip() or None,
                customer_phone=request.form.get("customer_phone", "").strip() or None,
                payment_mode=request.form.get("payment_mode", "cash"),
                discount_amount=float(request.form.get("discount_amount", 0) or 0),
                discount_note=request.form.get("discount_note", "").strip() or None,
                wallet_redeem_points=int(request.form.get("wallet_redeem_points", 0) or 0),
            )
            flash(f"Sale #{sale.id} created successfully.", "success")
            return redirect(url_for("sales.sale_detail", sale_id=sale.id))
        except InsufficientStockError as e:
            flash(str(e), "danger")
        except ValueError as e:
            flash(str(e), "danger")

    return render_template("sales/create.html", products=products)


@sales_bp.route("/<int:sale_id>")
@login_required
def sale_detail(sale_id):
    sale = sales_manager.get_sale(sale_id)
    return render_template("sales/detail.html", sale=sale)


@sales_bp.route("/<int:sale_id>/invoice")
@login_required
def download_invoice(sale_id):
    pdf_bytes = sales_manager.generate_invoice_pdf(sale_id)
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=invoice_{sale_id}.pdf"},
    )


@sales_bp.route("/<int:sale_id>/invoice/print")
@login_required
def print_invoice(sale_id):
    """Print-friendly HTML invoice — no sidebar, no topbar."""
    sale = sales_manager.get_sale(sale_id)
    from ...models.shop_settings import ShopSettings
    try:
        shop = ShopSettings.get()
    except Exception:
        shop = None
    return render_template("sales/print_invoice.html", sale=sale, shop=shop)


@sales_bp.route("/<int:sale_id>/receipt")
@login_required
def thermal_receipt(sale_id):
    """58mm thermal receipt — compact print layout."""
    sale = sales_manager.get_sale(sale_id)
    from ...models.shop_settings import ShopSettings
    try:
        shop = ShopSettings.get()
    except Exception:
        shop = None
    return render_template("sales/thermal_receipt.html", sale=sale, shop=shop)


@sales_bp.route("/customer-statement")
@login_required
def customer_statement():
    """Per-customer purchase history, credit, and loyalty statement."""
    from ...models.customer import Customer
    from ...models.sale import Sale, SaleItem
    from ...models.product import Product as _Product
    from ...models.shop_settings import ShopSettings
    from sqlalchemy import func

    customer_name = request.args.get("name", "").strip()
    customers = db.session.execute(
        db.select(Customer).order_by(Customer.name)
    ).scalars().all()

    statement = None
    shop = ShopSettings.get()

    if customer_name:
        sales = db.session.execute(
            db.select(Sale)
            .where(func.lower(Sale.customer_name) == customer_name.lower())
            .order_by(Sale.sale_date.desc())
        ).scalars().all()

        total_spent = sum(float(s.total_amount) for s in sales)
        total_discounts = sum(float(s.discount_amount or 0) for s in sales)
        credit_sales = [s for s in sales if s.payment_mode == "credit"]
        outstanding = sum(float(s.total_amount) for s in credit_sales if not s.credit_collected)
        collected = sum(float(s.total_amount) for s in credit_sales if s.credit_collected)

        # Payment mode breakdown
        pm_totals: dict = {}
        for s in sales:
            pm = s.payment_mode or "cash"
            pm_totals[pm] = pm_totals.get(pm, 0) + float(s.total_amount)

        # Top products bought
        top_products = db.session.execute(
            db.select(
                _Product.name,
                func.sum(SaleItem.quantity).label("qty"),
                func.sum(SaleItem.subtotal).label("total"),
            )
            .join(SaleItem, SaleItem.product_id == _Product.id)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(func.lower(Sale.customer_name) == customer_name.lower())
            .group_by(_Product.id, _Product.name)
            .order_by(func.sum(SaleItem.quantity).desc())
            .limit(5)
        ).all()

        # Loyalty points — use LoyaltyWallet (correct table)
        loyalty_points = 0
        loyalty_lifetime = 0
        loyalty_tier = "Silver"
        try:
            from ...models.ai_enhancements import LoyaltyWallet
            from ...models.customer import Customer as _Cust
            cust_obj = db.session.execute(
                db.select(_Cust).where(func.lower(_Cust.name) == customer_name.lower())
            ).scalar_one_or_none()
            if cust_obj:
                wallet = db.session.execute(
                    db.select(LoyaltyWallet).where(LoyaltyWallet.customer_id == cust_obj.id)
                ).scalar_one_or_none()
                if wallet:
                    loyalty_points = int(wallet.points_balance)
                    loyalty_lifetime = int(wallet.lifetime_points_earned)
                    loyalty_tier = wallet.tier or "Silver"
        except Exception:
            pass

        # Customer info
        cust_info = db.session.execute(
            db.select(Customer).where(func.lower(Customer.name) == customer_name.lower())
        ).scalar_one_or_none()

        statement = {
            "customer_name": customer_name,
            "customer": cust_info,
            "sales": sales,
            "total_spent": total_spent,
            "total_discounts": total_discounts,
            "total_transactions": len(sales),
            "outstanding_credit": outstanding,
            "collected_credit": collected,
            "pm_totals": pm_totals,
            "top_products": top_products,
            "loyalty_points": loyalty_points,
            "loyalty_lifetime": loyalty_lifetime,
            "loyalty_tier": loyalty_tier,
            "avg_order": round(total_spent / len(sales), 2) if sales else 0,
        }

    return render_template("sales/customer_statement.html",
                           customers=customers, statement=statement,
                           customer_name=customer_name,
                           shop=shop,
                           today=date.today())


@sales_bp.route("/credit-notes", methods=["GET", "POST"])
@login_required
def credit_notes():
    """Task 7: Credit notes list and creation."""
    from ...models.credit_note import CreditNote
    from ...models.sale import Sale as _Sale
    if request.method == "POST":
        sale_id = request.form.get("sale_id", "").strip()
        amount = request.form.get("amount", "").strip()
        reason = request.form.get("reason", "").strip() or None
        errors = []
        if not sale_id:
            errors.append("Sale ID is required.")
        if not amount:
            errors.append("Amount is required.")
        if errors:
            for msg in errors:
                flash(msg, "danger")
        else:
            try:
                cn = CreditNote(
                    sale_id=int(sale_id),
                    amount=float(amount),
                    reason=reason,
                    issued_by=current_user.id,
                )
                db.session.add(cn)
                db.session.commit()
                flash(f"Credit note #{cn.id} issued successfully.", "success")
                return redirect(url_for("sales.credit_notes"))
            except Exception as e:
                db.session.rollback()
                flash(f"Error creating credit note: {e}", "danger")
    notes = db.session.execute(
        db.select(CreditNote).order_by(CreditNote.created_at.desc()).limit(100)
    ).scalars().all()
    sales = db.session.execute(
        db.select(_Sale).order_by(_Sale.sale_date.desc()).limit(200)
    ).scalars().all()
    return render_template("sales/credit_notes.html", notes=notes, sales=sales)


@sales_bp.route("/<int:sale_id>/delete", methods=["POST"])
@admin_required
def delete_sale(sale_id):
    try:
        sales_manager.delete_sale(sale_id)
        flash(f"Sale #{sale_id} has been deleted and stock reversed.", "success")
    except Exception as e:
        flash(f"Error deleting sale: {e}", "danger")
    return redirect(url_for("sales.list_sales"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_items(form) -> list[dict]:
    """Parse items[N][product_id/quantity/unit_price] fields from form data."""
    items: list[dict] = []
    index = 0
    while True:
        product_id = form.get(f"items[{index}][product_id]")
        if product_id is None:
            break
        quantity_raw = form.get(f"items[{index}][quantity]", "0")
        unit_price_raw = form.get(f"items[{index}][unit_price]", "0")
        try:
            pid = int(product_id)
            qty = int(quantity_raw)
            price = float(unit_price_raw)
            if pid > 0 and qty > 0 and price >= 0:
                items.append({"product_id": pid, "quantity": qty, "unit_price": price})
        except (ValueError, TypeError):
            pass
        index += 1
    return items
