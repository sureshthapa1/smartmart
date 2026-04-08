"""AI Business Advisor blueprint."""
from flask import Blueprint, jsonify, render_template, request
from ...services.decorators import admin_required
from ...services import ai_business_advisor

advisor_bp = Blueprint("advisor", __name__, url_prefix="/advisor")


@advisor_bp.route("/")
@admin_required
def index():
    report = ai_business_advisor.full_advisor_report()
    return render_template("advisor/index.html", report=report)


@advisor_bp.route("/api/report")
@admin_required
def api_report():
    return jsonify(ai_business_advisor.full_advisor_report())


@advisor_bp.route("/api/forecast")
@admin_required
def api_forecast():
    return jsonify(ai_business_advisor.revenue_forecast_30d())


@advisor_bp.route("/api/kpis")
@admin_required
def api_kpis():
    return jsonify(ai_business_advisor.kpi_scorecard())
