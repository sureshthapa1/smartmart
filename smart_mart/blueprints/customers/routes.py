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


@customers_bp.route("/create", methods=["GET", "POST"])
@login_required
def create_customer():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip() or None
        address = request.form.get("address", "").strip() or None
        email = request.form.get("email", "").strip() or None
        if not name:
            flash("Customer name is required.", "danger")
            return render_template("customers/create.html")
        # Check duplicate phone
        if phone:
            existing = db.session.execute(
                db.select(Customer).where(Customer.phone == phone)
            ).scalar_one_or_none()
            if existing:
                flash(f"A customer with phone {phone} already exists.", "warning")
                return render_template("customers/create.html")
        customer = Customer(name=name, phone=phone, address=address)
        db.session.add(customer)
        db.session.commit()
        flash(f"Customer '{name}' added.", "success")
        return redirect(url_for("customers.customer_profile", customer_id=customer.id))
    return render_template("customers/create.html")


@customers_bp.route("/")
@login_required
def list_customers():
    q = request.args.get("q", "").strip() or None
    sort = request.args.get("sort", "visits")   # visits | name | recent
    page = request.args.get("page", 1, type=int)
    per_page = 30

    stmt = db.select(Customer)
    if q:
        term = f"%{q.lower()}%"
        stmt = stmt.where(
            func.lower(Customer.name).like(term) |
            Customer.phone.like(term)
        )
    if sort == "name":
        stmt = stmt.order_by(Customer.name)
    elif sort == "recent":
        stmt = stmt.order_by(Customer.last_visit.desc())
    else:
        stmt = stmt.order_by(Customer.visit_count.desc())

    total = db.session.execute(
        db.select(func.count()).select_from(stmt.subquery())
    ).scalar() or 0

    customers = db.session.execute(
        stmt.limit(per_page).offset((page - 1) * per_page)
    ).scalars().all()

    return render_template("customers/list.html",
                           customers=customers, q=q or "", sort=sort,
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

    return render_template("customers/profile.html",
                           customer=customer, sales=sales,
                           total_spent=total_spent, total_discount=total_discount,
                           credit_outstanding=credit_outstanding, avg_order=avg_order,
                           pm_counts=pm_counts)


@customers_bp.route("/<int:customer_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_customer(customer_id):
    customer = db.get_or_404(Customer, customer_id)
    if request.method == "POST":
        customer.name = request.form.get("name", "").strip() or customer.name
        customer.phone = request.form.get("phone", "").strip() or None
        customer.address = request.form.get("address", "").strip() or None
        db.session.commit()
        flash("Customer updated.", "success")
        return redirect(url_for("customers.customer_profile", customer_id=customer_id))
    return render_template("customers/edit.html", customer=customer)


@customers_bp.route("/<int:customer_id>/delete", methods=["POST"])
@admin_required
def delete_customer(customer_id):
    customer = db.get_or_404(Customer, customer_id)
    db.session.delete(customer)
    db.session.commit()
    flash(f"Customer '{customer.name}' deleted.", "success")
    return redirect(url_for("customers.list_customers"))


@customers_bp.route("/export-csv")
@admin_required
def export_csv():
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
