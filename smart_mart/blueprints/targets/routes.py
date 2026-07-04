from datetime import date, datetime, timezone

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from ...extensions import db
from ...models.sale import Sale
from ...models.sales_target import SalesTarget
from ...models.user import User
from ...services.decorators import login_required

targets_bp = Blueprint("targets", __name__, url_prefix="/targets")


def _admin_or_manager():
    if current_user.role not in ("admin", "manager"):
        abort(403)


@targets_bp.route("/")
@login_required
def index():
    _admin_or_manager()
    users = db.session.execute(db.select(User).order_by(User.username)).scalars().all()
    targets = db.session.execute(
        db.select(SalesTarget).order_by(SalesTarget.target_date.desc(), SalesTarget.id.desc()).limit(100)
    ).scalars().all()
    return render_template("targets/index.html", users=users, targets=targets, today=date.today())


@targets_bp.route("/set", methods=["POST"])
@login_required
def set_target():
    _admin_or_manager()
    try:
        user_id = int(request.form.get("user_id", 0) or 0)
        target_type = request.form.get("target_type", "daily")
        target_date = date.fromisoformat(request.form.get("target_date", ""))
        if target_type == "monthly":
            target_date = target_date.replace(day=1)
        amount = float(request.form.get("amount", 0) or 0)
        if amount <= 0:
            raise ValueError("Target amount must be greater than zero.")
        existing = db.session.execute(
            db.select(SalesTarget)
            .where(SalesTarget.user_id == user_id)
            .where(SalesTarget.target_type == target_type)
            .where(SalesTarget.target_date == target_date)
        ).scalar_one_or_none()
        if existing:
            existing.amount = amount
        else:
            db.session.add(SalesTarget(user_id=user_id, target_type=target_type, target_date=target_date, amount=amount))
        db.session.commit()
        flash("Sales target saved.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Could not save target: {exc}", "danger")
    return redirect(url_for("targets.index"))


@targets_bp.route("/progress")
@login_required
def progress():
    return jsonify(current_target_progress(current_user.id))


@targets_bp.route("/leaderboard")
@login_required
def leaderboard():
    _admin_or_manager()
    today = date.today()
    month_start = today.replace(day=1)
    targets = db.session.execute(
        db.select(SalesTarget)
        .where(SalesTarget.target_type == "monthly", SalesTarget.target_date == month_start)
        .order_by(SalesTarget.amount.desc())
    ).scalars().all()
    rows = []
    for target in targets:
        achieved = _achieved(target.user_id, month_start, today)
        amount = float(target.amount or 0)
        pct = round(achieved / amount * 100, 1) if amount else 0
        rows.append({"target": target, "achieved": achieved, "pct": pct})
    rows.sort(key=lambda row: row["pct"], reverse=True)
    return render_template("targets/leaderboard.html", rows=rows)


def current_target_progress(user_id):
    today = date.today()
    target = db.session.execute(
        db.select(SalesTarget)
        .where(SalesTarget.user_id == user_id)
        .where(SalesTarget.target_type == "daily")
        .where(SalesTarget.target_date == today)
    ).scalar_one_or_none()
    if not target:
        return {"has_target": False}
    achieved = _achieved(user_id, today, today)
    amount = float(target.amount or 0)
    pct = round(achieved / amount * 100, 1) if amount else 0
    color = "danger" if pct < 50 else "warning" if pct < 80 else "success"
    return {"has_target": True, "target": amount, "achieved": achieved, "pct": pct, "color": color}


def _achieved(user_id, start, end):
    return float(db.session.execute(
        db.select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(Sale.user_id == user_id)
        .where(Sale.sale_date >= start)
        .where(Sale.sale_date <= end)
    ).scalar() or 0)
