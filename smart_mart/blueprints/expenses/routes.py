"""Expenses blueprint — CRUD for rent, salary, utilities, and other costs."""

from __future__ import annotations

from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ...extensions import db
from ...models.expense import Expense
from ...services.decorators import admin_required, login_required

expenses_bp = Blueprint("expenses", __name__, url_prefix="/expenses")

EXPENSE_TYPES = ["rent", "salary", "utilities", "purchase", "miscellaneous", "other"]


def _require_perm(perm: str):
    """Abort 403 if staff user lacks the given permission."""
    from flask import abort
    from flask_login import current_user
    if current_user.role != "admin":
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(current_user.id)
        if not getattr(p, perm, False):
            abort(403)


@expenses_bp.route("/")
@login_required
def list_expenses():
    _require_perm("can_view_expenses")
    start_raw = request.args.get("start_date", "")
    end_raw = request.args.get("end_date", "")
    type_filter = request.args.get("type", "")
    page = int(request.args.get("page", 1))
    per_page = 50

    stmt = db.select(Expense).order_by(Expense.expense_date.desc(), Expense.id.desc())

    if start_raw:
        try:
            stmt = stmt.where(Expense.expense_date >= date.fromisoformat(start_raw))
        except ValueError:
            pass
    if end_raw:
        try:
            stmt = stmt.where(Expense.expense_date <= date.fromisoformat(end_raw))
        except ValueError:
            pass
    if type_filter:
        stmt = stmt.where(Expense.expense_type == type_filter)

    all_expenses = db.session.execute(stmt).scalars().all()
    total = len(all_expenses)
    expenses = all_expenses[(page - 1) * per_page: page * per_page]

    total_amount = sum(float(e.amount) for e in all_expenses)

    # By-type totals for summary cards and donut chart
    by_type: dict = {}
    for e in all_expenses:
        by_type[e.expense_type] = by_type.get(e.expense_type, 0) + float(e.amount)

    # Monthly breakdown for stacked bar chart (last 6 months, all data)
    from sqlalchemy import extract
    monthly_rows = db.session.execute(
        db.select(
            db.func.strftime('%Y-%m', Expense.expense_date).label("month"),
            Expense.expense_type.label("type"),
            db.func.sum(Expense.amount).label("total"),
        )
        .group_by(db.func.strftime('%Y-%m', Expense.expense_date), Expense.expense_type)
        .order_by(db.func.strftime('%Y-%m', Expense.expense_date))
    ).all()
    monthly_chart = [{"month": r.month, "type": r.type, "total": float(r.total)} for r in monthly_rows]

    return render_template("expenses/list.html",
                           expenses=expenses,
                           total_amount=total_amount,
                           total=total,
                           page=page,
                           per_page=per_page,
                           start_date=start_raw,
                           end_date=end_raw,
                           type_filter=type_filter,
                           expense_types=EXPENSE_TYPES,
                           by_type=by_type,
                           monthly_chart=monthly_chart)


@expenses_bp.route("/create", methods=["GET", "POST"])
@login_required
def create_expense():
    _require_perm("can_manage_expenses")
    if request.method == "POST":
        expense_type = request.form.get("expense_type", "").strip()
        amount_raw = request.form.get("amount", "").strip()
        date_raw = request.form.get("expense_date", "").strip()
        note = request.form.get("note", "").strip() or None

        errors = []
        if not expense_type or expense_type not in EXPENSE_TYPES:
            errors.append("Please select a valid expense type.")
        try:
            amount = float(amount_raw)
            if amount <= 0:
                errors.append("Amount must be greater than zero.")
        except (ValueError, TypeError):
            errors.append("Please enter a valid amount.")
            amount = 0
        try:
            expense_date = date.fromisoformat(date_raw)
            if expense_date > date.today():
                errors.append("Expense date cannot be in the future.")
        except (ValueError, TypeError):
            errors.append("Please enter a valid date.")
            expense_date = date.today()

        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template("expenses/form.html", expense=None,
                                   expense_types=EXPENSE_TYPES,
                                   form_data=request.form,
                                   today=date.today().isoformat())

        expense = Expense(
            expense_type=expense_type,
            amount=amount,
            expense_date=expense_date,
            note=note,
            created_by=current_user.id,
        )
        db.session.add(expense)
        db.session.commit()
        flash(f"Expense of NPR {amount:,.2f} ({expense_type}) recorded.", "success")
        return redirect(url_for("expenses.list_expenses"))

    return render_template("expenses/form.html", expense=None,
                           expense_types=EXPENSE_TYPES, form_data={},
                           today=date.today().isoformat())


@expenses_bp.route("/<int:expense_id>/edit", methods=["GET", "POST"])
@login_required
def edit_expense(expense_id):
    _require_perm("can_manage_expenses")
    expense = db.get_or_404(Expense, expense_id)

    if request.method == "POST":
        expense_type = request.form.get("expense_type", "").strip()
        amount_raw = request.form.get("amount", "").strip()
        date_raw = request.form.get("expense_date", "").strip()
        note = request.form.get("note", "").strip() or None

        errors = []
        if not expense_type or expense_type not in EXPENSE_TYPES:
            errors.append("Please select a valid expense type.")
        try:
            amount = float(amount_raw)
            if amount <= 0:
                errors.append("Amount must be greater than zero.")
        except (ValueError, TypeError):
            errors.append("Please enter a valid amount.")
            amount = float(expense.amount)
        try:
            expense_date = date.fromisoformat(date_raw)
        except (ValueError, TypeError):
            errors.append("Please enter a valid date.")
            expense_date = expense.expense_date

        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template("expenses/form.html", expense=expense,
                                   expense_types=EXPENSE_TYPES, form_data=request.form,
                                   today=date.today().isoformat())

        expense.expense_type = expense_type
        expense.amount = amount
        expense.expense_date = expense_date
        expense.note = note
        db.session.commit()
        flash("Expense updated.", "success")
        return redirect(url_for("expenses.list_expenses"))

    return render_template("expenses/form.html", expense=expense,
                           expense_types=EXPENSE_TYPES, form_data={},
                           today=date.today().isoformat())


@expenses_bp.route("/<int:expense_id>/delete", methods=["POST"])
@login_required
def delete_expense(expense_id):
    _require_perm("can_manage_expenses")
    expense = db.get_or_404(Expense, expense_id)
    db.session.delete(expense)
    db.session.commit()
    flash("Expense deleted.", "success")
    return redirect(url_for("expenses.list_expenses"))
