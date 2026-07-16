"""AI Business Advisor blueprint — rule-based analysis + Gemini narrative commentary."""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request
from ...services.decorators import admin_required, login_required
from ...services import ai_business_advisor

advisor_bp = Blueprint("advisor", __name__, url_prefix="/advisor")


def _require_perm(perm: str):
    from flask import abort
    from flask_login import current_user as _cu
    if _cu.role != "admin":
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(_cu.id)
        if not getattr(p, perm, False):
            abort(403)


# ── Claude narrative overlay ──────────────────────────────────────────────────

def _claude_advisor_commentary(report: dict) -> str | None:
    """
    Ask Gemini to write a short executive commentary on the full advisor report.
    Returns plain text paragraph or None if API key missing / call fails.
    """
    from ...services.gemini_client import gemini_generate, gemini_available
    if not gemini_available():
        return None
    try:
        summary = report.get("summary", {})
        recs    = report.get("recommendations", [])[:3]
        kpis    = report.get("kpis", [])

        condensed = {
            "monthly_revenue":    summary.get("revenue", {}).get("month", 0),
            "revenue_change_pct": summary.get("revenue", {}).get("change_pct", 0),
            "gross_margin_pct":   summary.get("margins", {}).get("gross", 0),
            "net_profit":         summary.get("profit", {}).get("month", 0),
            "out_of_stock":       summary.get("stock", {}).get("out", 0),
            "low_stock":          summary.get("stock", {}).get("low", 0),
            "top_issues":         [r.get("title", "") for r in recs],
            "kpi_scores":         {k.get("kpi"): k.get("score") for k in kpis},
        }

        import json as _json
        prompt = (
            f"Business data for GoldKernel dry fruits retail shop (Dhangadhi, Nepal): "
            f"{_json.dumps(condensed)}\n\n"
            "Give a 2-3 sentence executive summary of the business health and the single most "
            "important action to take right now. Be direct and specific. Currency is NPR. No markdown."
        )
        system = (
            "You are a concise business advisor for GoldKernel, a premium dry fruits "
            "retail shop in Dhangadhi, Nepal. Currency is NPR."
        )
        return gemini_generate(prompt, system=system, max_tokens=220)
    except Exception:
        return None


# ── Routes ────────────────────────────────────────────────────────────────────

@advisor_bp.route("/")
@login_required
def index():
    _require_perm("can_view_advisor")
    report = ai_business_advisor.full_advisor_report()
    commentary = _claude_advisor_commentary(report)
    return render_template("advisor/index.html", report=report, commentary=commentary)


@advisor_bp.route("/api/report")
@login_required
def api_report():
    _require_perm("can_view_advisor")
    report = ai_business_advisor.full_advisor_report()
    commentary = _claude_advisor_commentary(report)
    return jsonify({**report, "ai_commentary": commentary})


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


@advisor_bp.route("/api/commentary")
@login_required
def api_commentary():
    """Just the AI commentary — called via AJAX to avoid blocking page load."""
    _require_perm("can_view_advisor")
    report = ai_business_advisor.full_advisor_report()
    commentary = _claude_advisor_commentary(report)
    return jsonify({"commentary": commentary, "ai_enhanced": commentary is not None})
