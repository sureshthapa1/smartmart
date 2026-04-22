"""Finance blueprint — period close/lock and accounting exports."""
from __future__ import annotations

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ...services import period_service
from ...services.decorators import admin_required

finance_bp = Blueprint("finance", __name__, url_prefix="/finance")


@finance_bp.route("/periods")
@admin_required
def periods():
    all_periods = period_service.list_periods()
    return render_template("finance/periods.html", periods=all_periods)


@finance_bp.route("/periods/close", methods=["POST"])
@admin_required
def close_period():
    year = request.form.get("year", type=int)
    month = request.form.get("month", type=int)
    notes = request.form.get("notes", "").strip() or None
    if not year or not month:
        flash("Year and month are required.", "danger")
        return redirect(url_for("finance.periods"))
    try:
        p = period_service.close_period(year, month, current_user.id, notes)
        flash(f"Period {p.label} closed successfully.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("finance.periods"))


@finance_bp.route("/periods/<int:period_id>/reopen", methods=["POST"])
@admin_required
def reopen_period(period_id):
    try:
        p = period_service.reopen_period(period_id, current_user.id)
        flash(f"Period {p.label} reopened.", "info")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("finance.periods"))


@finance_bp.route("/periods/<int:period_id>/lock", methods=["POST"])
@admin_required
def lock_period(period_id):
    try:
        p = period_service.lock_period(period_id)
        flash(f"Period {p.label} is now permanently locked.", "warning")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("finance.periods"))


@finance_bp.route("/periods/<int:period_id>/export/summary")
@admin_required
def export_summary(period_id):
    from ...models.financial_period import FinancialPeriod
    from ...extensions import db
    period = db.get_or_404(FinancialPeriod, period_id)
    csv_data = period_service.export_period_csv(period)
    filename = f"period_{period.year}_{period.month:02d}_summary.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@finance_bp.route("/periods/<int:period_id>/export/sales")
@admin_required
def export_sales(period_id):
    from ...models.financial_period import FinancialPeriod
    from ...extensions import db
    period = db.get_or_404(FinancialPeriod, period_id)
    csv_data = period_service.export_period_sales_csv(period.year, period.month)
    filename = f"period_{period.year}_{period.month:02d}_sales.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
