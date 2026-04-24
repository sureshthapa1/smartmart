"""Customers blueprint — customer list, profile, edit, and history."""
from __future__ import annotations

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from ...extensions import db
from ...models.customer import Customer
from ...models.sale import Sale
from ...services.decorators import login_required, admin_required

customers_bp = Blueprint("customers", __name__, url_prefix="/customers")

def _require_perm(perm: str):
    """Abort 403 if staff user lacks the given permission."""
    from flask import abort
    from flask_login import current_user as _cu
    if _cu.role != "admin":
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(_cu.id)
        if not getattr(p, perm, False):
            abort(403)




@customers_bp.route("/create", methods=["GET", "POST"])
@login_required
def create_customer():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip() or None
        address = request.form.get("address", "").strip() or None
        email = request.form.get("email", "").strip() or None
        birthday_raw = request.form.get("birthday", "").strip() or None
        if not name:
            flash("Customer name is required.", "danger")
            return render_template("customers/create.html")
        if phone:
            existing = db.session.execute(
                db.select(Customer).where(Customer.phone == phone)
            ).scalar_one_or_none()
            if existing:
                flash(f"A customer with phone {phone} already exists.", "warning")
                return render_template("customers/create.html")
        from datetime import date as _date
        birthday = None
        if birthday_raw:
            try:
                birthday = _date.fromisoformat(birthday_raw)
            except ValueError:
                pass
        customer = Customer(name=name, phone=phone, address=address, email=email, birthday=birthday)
        db.session.add(customer)
        db.session.commit()
        flash(f"Customer '{name}' added.", "success")
        return redirect(url_for("customers.customer_profile", customer_id=customer.id))
    return render_template("customers/create.html")


@customers_bp.route("/")
@login_required
def list_customers():
    q = request.args.get("q", "").strip() or None
    sort = request.args.get("sort", "visits")
    page = request.args.get("page", 1, type=int)
    # Default: only show customers with at least 1 real sale
    # "all" shows everyone including 0-sale / manually created
    show = request.args.get("show", "real")
    per_page = 30

    # Join with actual sale count from the sales table (source of truth)
    sale_count_sq = (
        db.select(
            db.func.lower(Sale.customer_name).label("cname"),
            db.func.count(Sale.id).label("sale_count"),
        )
        .group_by(db.func.lower(Sale.customer_name))
        .subquery()
    )

    stmt = (
        db.select(Customer, db.func.coalesce(sale_count_sq.c.sale_count, 0).label("real_sales"))
        .outerjoin(sale_count_sq, db.func.lower(Customer.name) == sale_count_sq.c.cname)
    )

    if q:
        term = f"%{q.lower()}%"
        stmt = stmt.where(
            func.lower(Customer.name).like(term) |
            Customer.phone.like(term)
        )

    if show == "real":
        stmt = stmt.where(db.func.coalesce(sale_count_sq.c.sale_count, 0) >= 1)

    if sort == "name":
        stmt = stmt.order_by(Customer.name)
    elif sort == "recent":
        stmt = stmt.order_by(Customer.last_visit.desc())
    else:
        stmt = stmt.order_by(db.func.coalesce(sale_count_sq.c.sale_count, 0).desc())

    total = db.session.execute(
        db.select(func.count()).select_from(stmt.subquery())
    ).scalar() or 0

    rows = db.session.execute(
        stmt.limit(per_page).offset((page - 1) * per_page)
    ).all()

    # Build list of (customer, real_sales) tuples
    customers = [(row.Customer, row.real_sales) for row in rows]

    # Count of 0-sale customers for the toggle badge
    zero_sale_count = db.session.execute(
        db.select(func.count(Customer.id))
        .outerjoin(sale_count_sq, db.func.lower(Customer.name) == sale_count_sq.c.cname)
        .where(db.func.coalesce(sale_count_sq.c.sale_count, 0) == 0)
    ).scalar() or 0

    return render_template("customers/list.html",
                           customers=customers, q=q or "", sort=sort,
                           show=show, zero_sale_count=zero_sale_count,
                           page=page, per_page=per_page, total=total)


@customers_bp.route("/<int:customer_id>")
@login_required
def customer_profile(customer_id):
    customer = db.get_or_404(Customer, customer_id)
    sales = db.session.execute(
        db.select(Sale)
        .where(func.lower(Sale.customer_name) == customer.name.lower())
        .order_by(Sale.sale_date.desc())
        .limit(50)
    ).scalars().all()

    total_spent = sum(float(s.total_amount) for s in sales)
    total_discount = sum(float(s.discount_amount or 0) for s in sales)
    credit_outstanding = sum(
        float(s.total_amount) for s in sales
        if s.payment_mode == "credit" and not s.credit_collected
    )
    avg_order = round(total_spent / len(sales), 2) if sales else 0

    # Payment mode breakdown
    pm_counts: dict = {}
    for s in sales:
        pm = s.payment_mode or "cash"
        pm_counts[pm] = pm_counts.get(pm, 0) + 1

    # Loyalty wallet
    wallet = None
    wallet_transactions = []
    try:
        from ...models.ai_enhancements import LoyaltyWallet, LoyaltyWalletTransaction
        wallet = db.session.execute(
            db.select(LoyaltyWallet).where(LoyaltyWallet.customer_id == customer.id)
        ).scalar_one_or_none()
        if wallet:
            wallet_transactions = db.session.execute(
                db.select(LoyaltyWalletTransaction)
                .where(LoyaltyWalletTransaction.wallet_id == wallet.id)
                .order_by(LoyaltyWalletTransaction.id.desc())
                .limit(20)
            ).scalars().all()
    except Exception:
        pass

    return render_template("customers/profile.html",
                           customer=customer, sales=sales,
                           total_spent=total_spent, total_discount=total_discount,
                           credit_outstanding=credit_outstanding, avg_order=avg_order,
                           pm_counts=pm_counts,
                           wallet=wallet, wallet_transactions=wallet_transactions)

@customers_bp.route("/<int:customer_id>/edit", methods=["GET", "POST"])
@login_required
def edit_customer(customer_id):
    _require_perm("can_manage_customers")
    customer = db.get_or_404(Customer, customer_id)
    if request.method == "POST":
        customer.name = request.form.get("name", "").strip() or customer.name
        customer.phone = request.form.get("phone", "").strip() or None
        customer.address = request.form.get("address", "").strip() or None
        try:
            customer.email = request.form.get("email", "").strip() or None
        except Exception:
            pass
        birthday_raw = request.form.get("birthday", "").strip()
        if birthday_raw:
            try:
                from datetime import date as _date
                customer.birthday = _date.fromisoformat(birthday_raw)
            except ValueError:
                pass
        elif request.form.get("clear_birthday") == "1":
            try:
                customer.birthday = None
            except Exception:
                pass
        try:
            db.session.commit()
            flash("Customer updated.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating customer: {e}", "danger")
        return redirect(url_for("customers.customer_profile", customer_id=customer_id))
    return render_template("customers/edit.html", customer=customer)


@customers_bp.route("/<int:customer_id>/delete", methods=["POST"])
@login_required
def delete_customer(customer_id):
    _require_perm("can_manage_customers")
    customer = db.get_or_404(Customer, customer_id)
    try:
        # Delete related records that have FK to customers (PostgreSQL requires this)
        from ...models.ai_enhancements import LoyaltyWallet, LoyaltyWalletTransaction, CustomerDuplicateFlag
        # Delete loyalty wallet transactions first, then wallet
        wallet = db.session.execute(
            db.select(LoyaltyWallet).where(LoyaltyWallet.customer_id == customer_id)
        ).scalar_one_or_none()
        if wallet:
            db.session.execute(
                db.delete(LoyaltyWalletTransaction).where(LoyaltyWalletTransaction.wallet_id == wallet.id)
            )
            db.session.delete(wallet)
        # Delete duplicate flags
        db.session.execute(
            db.delete(CustomerDuplicateFlag).where(
                db.or_(
                    CustomerDuplicateFlag.primary_customer_id == customer_id,
                    CustomerDuplicateFlag.duplicate_customer_id == customer_id,
                )
            )
        )
        db.session.flush()
        db.session.delete(customer)
        db.session.commit()
        flash(f"Customer '{customer.name}' deleted.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Could not delete customer: {e}", "danger")
    return redirect(url_for("customers.list_customers"))


@customers_bp.route("/<int:customer_id>/loyalty-card")
@login_required
def loyalty_card(customer_id):
    """Printable loyalty card with QR code."""
    customer = db.get_or_404(Customer, customer_id)

    # Get loyalty wallet
    wallet = None
    try:
        from ...models.ai_enhancements import LoyaltyWallet
        wallet = db.session.execute(
            db.select(LoyaltyWallet).where(LoyaltyWallet.customer_id == customer.id)
        ).scalar_one_or_none()
    except Exception:
        pass

    # Get shop settings
    from ...models.shop_settings import ShopSettings
    shop = ShopSettings.get()

    # Build QR data — use configured site URL or request host
    from flask import current_app
    site_url = current_app.config.get("SITE_URL", "").rstrip("/")
    if not site_url:
        from flask import request as _req
        site_url = _req.host_url.rstrip("/")
    qr_data = f"{site_url}/sales/customer-statement?name={customer.name}"

    # Generate QR code as base64 PNG (no external API)
    qr_image_b64 = _generate_qr_b64(qr_data)

    return render_template("customers/loyalty_card.html",
                           customer=customer,
                           wallet=wallet,
                           shop=shop,
                           qr_image_b64=qr_image_b64,
                           qr_data=qr_data)


def _generate_qr_b64(data: str) -> str:
    """Generate a QR code and return it as a base64-encoded PNG data URI."""
    try:
        import qrcode
        import io
        import base64
        qr = qrcode.QRCode(version=1, box_size=4, border=2)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0f172a", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/png;base64,{b64}"
    except Exception:
        return ""


@customers_bp.route("/export-csv")
@login_required
def export_csv():
    _require_perm("can_view_customers")
    import csv, io
    customers = db.session.execute(
        db.select(Customer).order_by(Customer.visit_count.desc())
    ).scalars().all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name", "Phone", "Address", "Visits", "Last Visit", "Created"])
    for c in customers:
        writer.writerow([
            c.name, c.phone or "", c.address or "",
            c.visit_count,
            c.last_visit.strftime("%Y-%m-%d") if c.last_visit else "",
            c.created_at.strftime("%Y-%m-%d") if c.created_at else "",
        ])
    return Response(
        output.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=customers.csv"}
    )


@customers_bp.route("/birthdays-today")
@login_required
def birthdays_today():
    """Return customers with birthday today or in next 7 days."""
    from datetime import date, timedelta
    today = date.today()
    customers = db.session.execute(db.select(Customer).where(Customer.birthday.isnot(None))).scalars().all()
    upcoming = []
    for c in customers:
        if c.birthday:
            bday_this_year = c.birthday.replace(year=today.year)
            if bday_this_year < today:
                bday_this_year = c.birthday.replace(year=today.year + 1)
            days_until = (bday_this_year - today).days
            if 0 <= days_until <= 7:
                upcoming.append({
                    "id": c.id, "name": c.name, "phone": c.phone or "",
                    "birthday": c.birthday.strftime("%b %d"),
                    "days_until": days_until,
                    "is_today": days_until == 0,
                })
    upcoming.sort(key=lambda x: x["days_until"])
    from flask import jsonify
    return jsonify(upcoming)
