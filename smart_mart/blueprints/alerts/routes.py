"""Alerts blueprint — low stock, expiry, and high-demand alerts."""

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from ...extensions import db
from ...models.dismissed_alert import DismissedAlert
from ...services import alert_engine
from ...services.decorators import login_required

alerts_bp = Blueprint("alerts", __name__, url_prefix="/alerts")


def _dismissed_keys(user_id: int) -> set[str]:
    rows = db.session.execute(
        db.select(DismissedAlert.alert_key).where(DismissedAlert.user_id == user_id)
    ).scalars().all()
    return set(rows)


@alerts_bp.route("/")
@login_required
def index():
    dismissed = _dismissed_keys(current_user.id)
    alerts = alert_engine.get_all_alerts()
    return render_template("alerts/index.html", alerts=alerts, dismissed=dismissed)


@alerts_bp.route("/dismiss", methods=["POST"])
@login_required
def dismiss():
    """Mark all current alerts as read/dismissed for this user."""
    alerts = alert_engine.get_all_alerts()
    keys = (
        [f"low_stock:{p.id}" for p in alerts["low_stock"]]
        + [f"expiry:{p.id}" for p in alerts["expiry"]]
        + [f"high_demand:{item['product'].id}" for item in alerts["high_demand"]]
    )
    existing = _dismissed_keys(current_user.id)
    for key in keys:
        if key not in existing:
            db.session.add(DismissedAlert(user_id=current_user.id, alert_key=key))
    db.session.commit()
    return redirect(url_for("alerts.index"))


@alerts_bp.route("/api/summary")
@login_required
def api_summary():
    """JSON endpoint for the navbar bell dropdown."""
    dismissed = _dismissed_keys(current_user.id)
    alerts = alert_engine.get_all_alerts()

    items = []
    for p in alerts["low_stock"]:
        if f"low_stock:{p.id}" not in dismissed:
            items.append({"type": "low_stock", "label": f"{p.name} — only {p.quantity} left", "icon": "exclamation-triangle-fill", "color": "warning"})
    for p in alerts["expiry"]:
        if f"expiry:{p.id}" not in dismissed:
            items.append({"type": "expiry", "label": f"{p.name} expires {p.expiry_date.strftime('%Y-%m-%d')}", "icon": "calendar-x-fill", "color": "danger"})
    for item in alerts["high_demand"]:
        if f"high_demand:{item['product'].id}" not in dismissed:
            items.append({"type": "high_demand", "label": f"{item['product'].name} — {item['total_sold']} sold (7d)", "icon": "fire", "color": "info"})

    return jsonify({"count": len(items), "items": items[:5]})
