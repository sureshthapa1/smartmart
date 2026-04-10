"""AI Business Advisor blueprint."""
from flask import Blueprint, jsonify, render_template, request
from ...services.decorators import admin_required, login_required
from ...services import ai_business_advisor

advisor_bp = Blueprint("advisor", __name__, url_prefix="/advisor")

def _require_perm(perm: str):
    """Abort 403 if staff user lacks the given permission."""
    from flask import abort
    from flask_login import current_user as _cu
    if _cu.role != "admin":
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(_cu.id)
        if not getattr(p, perm, False):
            abort(403)




@advisor_bp.route("/")
@login_required
def index():
    _require_perm("can_view_advisor")
    report = ai_business_advisor.full_advisor_report()
    return render_template("advisor/index.html", report=report)


@advisor_bp.route("/api/report")
@login_required
def api_report():
    _require_perm("can_view_advisor")
    return jsonify(ai_business_advisor.full_advisor_report())


@advisor_bp.route("/api/forecast")
@login_required
def api_forecast():
    _require_perm("can_view_advisor")
    return jsonify(ai_business_advisor.revenue_forecast_30d())


@advisor_bp.route("/api/kpis")
@login_required
def api_kpis():
    _require_perm("can_view_advisor")
    return jsonify(ai_business_advisor.kpi_scorecard())


@advisor_bp.route("/api/product-actions")
@login_required
def api_product_actions():
    _require_perm("can_view_advisor")
    return jsonify(ai_business_advisor.product_action_recommendations())
