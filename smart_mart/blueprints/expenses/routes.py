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
    from ...services.db_compat import date_format_year_month as _ym
    monthly_rows = db.session.execute(
        db.select(
            _ym(Expense.expense_date).label("month"),
            Expense.expense_type.label("type"),
            db.func.sum(Expense.amount).label("total"),
        )
        .group_by(_ym(Expense.expense_date), Expense.expense_type)
        .order_by(_ym(Expense.expense_date))
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
        db.session.flush()
        from ...services.expense_sync import sync_create
        sync_create(expense)
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
        from ...services.expense_sync import sync_update
        sync_update(expense)
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
    bi_opex_id = getattr(expense, "bi_opex_id", None)
    db.session.delete(expense)
    from ...services.expense_sync import sync_delete
    sync_delete(bi_opex_id)
    db.session.commit()
    flash("Expense deleted.", "success")
    return redirect(url_for("expenses.list_expenses"))


# ── Recurring Expenses ────────────────────────────────────────────────────────

FREQUENCIES = [
    ("weekly", "Weekly"),
    ("monthly", "Monthly"),
    ("quarterly", "Every 3 Months"),
    ("yearly", "Yearly"),
    ("custom", "Custom (specify days)"),
]


@expenses_bp.route("/recurring")
@login_required
def list_recurring():
    _require_perm("can_view_expenses")
    from ...models.recurring_expense import RecurringExpense
    from datetime import date, timedelta
    today = date.today()
    items = db.session.execute(
        db.select(RecurringExpense)
        .where(RecurringExpense.is_active == True)
        .order_by(RecurringExpense.next_due_date.asc())
    ).scalars().all()
    # Annotate with days_until
    for item in items:
        item.days_until = (item.next_due_date - today).days
    return render_template("expenses/recurring_list.html",
                           items=items, today=today, frequencies=FREQUENCIES)


@expenses_bp.route("/recurring/create", methods=["GET", "POST"])
@login_required
def create_recurring():
    _require_perm("can_manage_expenses")
    from ...models.recurring_expense import RecurringExpense
    from datetime import date
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        expense_type = request.form.get("expense_type", "other")
        amount_raw = request.form.get("amount", "0")
        frequency = request.form.get("frequency", "monthly")
        freq_days = request.form.get("frequency_days", "").strip()
        due_raw = request.form.get("next_due_date", "")
        reminder = int(request.form.get("reminder_days", 7) or 7)
        notes = request.form.get("notes", "").strip() or None

        errors = []
        if not name:
            errors.append("Name is required.")
        try:
            amount = float(amount_raw)
            if amount <= 0:
                errors.append("Amount must be greater than zero.")
        except (ValueError, TypeError):
            errors.append("Invalid amount.")
            amount = 0
        try:
            next_due = date.fromisoformat(due_raw)
        except (ValueError, TypeError):
            errors.append("Invalid due date.")
            next_due = date.today()

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("expenses/recurring_form.html",
                                   item=None, frequencies=FREQUENCIES,
                                   expense_types=EXPENSE_TYPES,
                                   today=date.today().isoformat())

        item = RecurringExpense(
            name=name, expense_type=expense_type, amount=amount,
            frequency=frequency,
            frequency_days=int(freq_days) if freq_days and frequency == "custom" else None,
            next_due_date=next_due, reminder_days=reminder,
            notes=notes, created_by=current_user.id,
        )
        db.session.add(item)
        db.session.commit()
        flash(f"Recurring expense '{name}' created.", "success")
        return redirect(url_for("expenses.list_recurring"))

    from datetime import date
    return render_template("expenses/recurring_form.html",
                           item=None, frequencies=FREQUENCIES,
                           expense_types=EXPENSE_TYPES,
                           today=date.today().isoformat())


@expenses_bp.route("/recurring/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def edit_recurring(item_id):
    _require_perm("can_manage_expenses")
    from ...models.recurring_expense import RecurringExpense
    from datetime import date
    item = db.get_or_404(RecurringExpense, item_id)
    if request.method == "POST":
        item.name = request.form.get("name", "").strip() or item.name
        item.expense_type = request.form.get("expense_type", item.expense_type)
        try:
            item.amount = float(request.form.get("amount", item.amount))
        except (ValueError, TypeError):
            pass
        item.frequency = request.form.get("frequency", item.frequency)
        freq_days = request.form.get("frequency_days", "").strip()
        item.frequency_days = int(freq_days) if freq_days and item.frequency == "custom" else None
        due_raw = request.form.get("next_due_date", "")
        try:
            item.next_due_date = date.fromisoformat(due_raw)
        except (ValueError, TypeError):
            pass
        item.reminder_days = int(request.form.get("reminder_days", 7) or 7)
        item.notes = request.form.get("notes", "").strip() or None
        db.session.commit()
        flash("Recurring expense updated.", "success")
        return redirect(url_for("expenses.list_recurring"))
    return render_template("expenses/recurring_form.html",
                           item=item, frequencies=FREQUENCIES,
                           expense_types=EXPENSE_TYPES,
                           today=item.next_due_date.isoformat())


@expenses_bp.route("/recurring/<int:item_id>/pay", methods=["POST"])
@login_required
def pay_recurring(item_id):
    """Mark as paid — creates an expense record and advances next due date."""
    _require_perm("can_manage_expenses")
    from ...models.recurring_expense import RecurringExpense
    from datetime import date
    item = db.get_or_404(RecurringExpense, item_id)
    # Create actual expense record
    expense = Expense(
        expense_type=item.expense_type,
        amount=item.amount,
        expense_date=date.today(),
        note=f"{item.name} (recurring)",
        created_by=current_user.id,
    )
    db.session.add(expense)
    db.session.flush()
    from ...services.expense_sync import sync_create
    sync_create(expense)
    # Advance next due date
    item.last_paid_at = date.today()
    item.next_due_date = item.next_due_after_payment()
    db.session.commit()
    flash(f"'{item.name}' marked as paid. Next due: {item.next_due_date.strftime('%d %b %Y')}.", "success")
    return redirect(url_for("expenses.list_recurring"))


@expenses_bp.route("/recurring/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_recurring(item_id):
    _require_perm("can_manage_expenses")
    from ...models.recurring_expense import RecurringExpense
    item = db.get_or_404(RecurringExpense, item_id)
    item.is_active = False  # soft delete
    db.session.commit()
    flash(f"'{item.name}' deactivated.", "info")
    return redirect(url_for("expenses.list_recurring"))


@expenses_bp.route("/recurring/due-reminders")
@login_required
def due_reminders():
    """JSON endpoint — returns recurring expenses due within reminder window."""
    from ...models.recurring_expense import RecurringExpense
    from datetime import date
    from flask import jsonify
    today = date.today()
    items = db.session.execute(
        db.select(RecurringExpense)
        .where(RecurringExpense.is_active == True)
    ).scalars().all()
    due = []
    for item in items:
        days_until = (item.next_due_date - today).days
        if days_until <= item.reminder_days:
            due.append({
                "id": item.id,
                "name": item.name,
                "amount": float(item.amount),
                "next_due": item.next_due_date.isoformat(),
                "days_until": days_until,
                "overdue": days_until < 0,
                "frequency": item.frequency_label,
            })
    due.sort(key=lambda x: x["days_until"])
    return jsonify(due)
