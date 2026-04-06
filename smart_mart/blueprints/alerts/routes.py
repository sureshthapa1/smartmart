"""Alerts blueprint — low stock, expiry, and high-demand alerts."""

from flask import Blueprint, render_template
from ...services import alert_engine
from ...services.decorators import login_required

alerts_bp = Blueprint("alerts", __name__, url_prefix="/alerts")


@alerts_bp.route("/")
@login_required
def index():
    alerts = alert_engine.get_all_alerts()
    return render_template("alerts/index.html", alerts=alerts)
