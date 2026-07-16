"""
ai_agent_runner.py — Automated AI Agent Pipeline
=================================================
Background agents that run on a schedule or trigger automatically.

Agents
------
1. DailyNLGAgent       — Generates a daily business summary and emails it to admin
2. AutoRestockAgent    — Detects low stock and drafts purchase orders
3. ChurnAlertAgent     — Identifies at-risk customers and logs alerts
4. PricingAgent        — Flags products with sub-optimal pricing
5. RAGIndexAgent       — Keeps the RAG product index fresh
6. AnomalyAlertAgent   — Detects and logs sales/stock anomalies

Entry points
------------
run_all_agents(user_id)          — Run all agents (called by /api/bot/run)
run_agent(agent_name, user_id)   — Run a specific agent

All agents are non-blocking and log results via the existing AppNotification model.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta, datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _notify(message: str, notification_type: str = "ai_agent") -> None:
    """Log an app notification without raising."""
    try:
        from ..extensions import db
        from ..models.operations import AppNotification
        n = AppNotification(
            notification_type=notification_type,
            message=message[:500],
        )
        db.session.add(n)
        db.session.commit()
    except Exception as exc:
        logger.warning("_notify failed: %s", exc)


def _send_email(subject: str, body_html: str, to_email: str | None = None) -> bool:
    """Send email via Flask-Mail if configured."""
    try:
        from flask import current_app
        mail = current_app.extensions.get("mail")
        if not mail:
            return False
        admin_email = to_email or os.environ.get("MAIL_USERNAME", "")
        if not admin_email:
            return False
        from flask_mail import Message
        msg = Message(subject=subject, recipients=[admin_email], html=body_html)
        mail.send(msg)
        return True
    except Exception as exc:
        logger.warning("_send_email failed: %s", exc)
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 1 — Daily NLG Summary
# ═══════════════════════════════════════════════════════════════════════════════

def run_daily_nlg_agent() -> dict[str, Any]:
    """Generate daily business summary and optionally email it."""
    try:
        from .ai_nlg import generate_daily_report
        report = generate_daily_report()
        summary = report.get("summary") or report.get("text") or str(report)[:500]

        # Try to enhance with Gemini if API key is set
        from .gemini_client import gemini_available
        if gemini_available():
            try:
                from .ai_business_advisor import executive_summary
                metrics = executive_summary()
                rev_today  = metrics["revenue"].get("week", 0)
                profit_m   = metrics["profit"].get("month", 0)
                enhanced = (
                    f"📊 Daily Summary — {date.today().isoformat()}\n"
                    f"Revenue this week: NPR {rev_today:,.0f}\n"
                    f"Profit this month: NPR {profit_m:,.0f}\n\n"
                    f"{summary}"
                )
                summary = enhanced
            except Exception:
                pass

        _notify(summary[:500], "daily_nlg_summary")

        # Email to admin
        email_sent = _send_email(
            subject=f"[GoldKernel] Daily Summary — {date.today().strftime('%b %d, %Y')}",
            body_html=f"<pre style='font-family:monospace'>{summary}</pre>",
        )

        return {"status": "ok", "summary_length": len(summary), "email_sent": email_sent}
    except Exception as exc:
        logger.exception("daily_nlg_agent failed: %s", exc)
        return {"status": "error", "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 2 — Auto-Restock
# ═══════════════════════════════════════════════════════════════════════════════

def run_auto_restock_agent(user_id: int = 1) -> dict[str, Any]:
    """Draft purchase orders for products running low."""
    try:
        from .ai_growth_ops import auto_replenishment_plan, create_auto_draft_purchase_orders
        plan = auto_replenishment_plan(lookback_days=30, safety_days=4, coverage_days=14)

        if not plan:
            return {"status": "ok", "drafted": 0, "message": "No restock needed"}

        result = create_auto_draft_purchase_orders(
            user_id=user_id,
            lookback_days=30,
            safety_days=4,
            coverage_days=14,
        )
        drafted_count = result.get("created", 0) if isinstance(result, dict) else 0

        if drafted_count:
            _notify(
                f"Auto-Restock: {drafted_count} purchase order draft(s) created by AI agent.",
                "auto_restock",
            )

        return {"status": "ok", "drafted": drafted_count, "plan_items": len(plan)}
    except Exception as exc:
        logger.exception("auto_restock_agent failed: %s", exc)
        return {"status": "error", "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 3 — Churn Alert
# ═══════════════════════════════════════════════════════════════════════════════

def run_churn_alert_agent() -> dict[str, Any]:
    """Identify customers at risk of churning and log alerts."""
    try:
        from ..extensions import db
        from ..models.customer import Customer
        from ..models.sale import Sale
        from sqlalchemy import func

        today = date.today()
        cutoff_45  = today - timedelta(days=45)
        cutoff_90  = today - timedelta(days=90)
        cutoff_180 = today - timedelta(days=180)

        # Customers who purchased 3+ months ago but not in 45 days
        at_risk = db.session.execute(
            db.select(
                Sale.customer_name,
                func.max(Sale.sale_date).label("last_purchase"),
                func.count(Sale.id).label("total_orders"),
                func.sum(Sale.total_amount).label("total_spent"),
            )
            .where(Sale.customer_name.isnot(None))
            .group_by(Sale.customer_name)
            .having(
                func.max(Sale.sale_date) < cutoff_45,
                func.max(Sale.sale_date) >= cutoff_180,
                func.count(Sale.id) >= 2,
            )
            .order_by(func.sum(Sale.total_amount).desc())
            .limit(20)
        ).all()

        if at_risk:
            names = ", ".join(r.customer_name for r in at_risk[:5])
            _notify(
                f"Churn Alert: {len(at_risk)} customers at risk. Top: {names}. "
                f"Consider sending re-engagement offers.",
                "churn_alert",
            )

        return {
            "status": "ok",
            "at_risk_count": len(at_risk),
            "top_at_risk": [
                {
                    "name": r.customer_name,
                    "last_purchase_days_ago": (today - r.last_purchase.date()).days,
                    "total_spent": float(r.total_spent or 0),
                }
                for r in at_risk[:10]
            ],
        }
    except Exception as exc:
        logger.exception("churn_alert_agent failed: %s", exc)
        return {"status": "error", "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 4 — Pricing Agent
# ═══════════════════════════════════════════════════════════════════════════════

def run_pricing_agent() -> dict[str, Any]:
    """Flag products with sub-optimal pricing and log recommendations."""
    try:
        from .ai_business_advisor import product_action_recommendations
        actions = product_action_recommendations()

        # Only surface highest-priority issues
        critical = [a for a in actions if a.get("priority") <= 2]
        if critical:
            names = ", ".join(a["product_name"] for a in critical[:3])
            _notify(
                f"Pricing Agent: {len(critical)} critical pricing issue(s). "
                f"Products: {names}. Review in AI Advisor.",
                "pricing_alert",
            )

        return {
            "status": "ok",
            "total_flags": len(actions),
            "critical": len(critical),
            "flags": [
                {"product": a["product_name"], "action": a["action_label"],
                 "reason": a["reason"][:100]}
                for a in critical[:10]
            ],
        }
    except Exception as exc:
        logger.exception("pricing_agent failed: %s", exc)
        return {"status": "error", "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 5 — RAG Index Refresh
# ═══════════════════════════════════════════════════════════════════════════════

def run_rag_index_agent() -> dict[str, Any]:
    """Force-rebuild the RAG product search index."""
    try:
        from .rag_service import build_index
        count = build_index(force=True)
        logger.info("RAG index refreshed: %d products", count)
        return {"status": "ok", "products_indexed": count}
    except Exception as exc:
        logger.exception("rag_index_agent failed: %s", exc)
        return {"status": "error", "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 6 — Anomaly Alert
# ═══════════════════════════════════════════════════════════════════════════════

def run_anomaly_alert_agent() -> dict[str, Any]:
    """Detect and log sales/stock anomalies."""
    try:
        from .ai_anomaly_detection import full_anomaly_report
        report = full_anomaly_report(7)  # last 7 days

        anomalies = report.get("anomalies", []) if isinstance(report, dict) else []
        if anomalies:
            first = anomalies[0]
            _notify(
                f"Anomaly detected: {first.get('description', str(first)[:100])}. "
                f"{len(anomalies)} anomaly(ies) total — check AI → Anomalies.",
                "anomaly_alert",
            )

        return {"status": "ok", "anomaly_count": len(anomalies)}
    except Exception as exc:
        logger.exception("anomaly_alert_agent failed: %s", exc)
        return {"status": "error", "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════════
# Dispatcher
# ═══════════════════════════════════════════════════════════════════════════════

AGENTS = {
    "daily_nlg":     run_daily_nlg_agent,
    "auto_restock":  run_auto_restock_agent,
    "churn_alert":   run_churn_alert_agent,
    "pricing":       run_pricing_agent,
    "rag_index":     run_rag_index_agent,
    "anomaly_alert": run_anomaly_alert_agent,
}


def run_agent(agent_name: str, user_id: int = 1) -> dict[str, Any]:
    fn = AGENTS.get(agent_name)
    if not fn:
        return {"status": "error", "error": f"Unknown agent: {agent_name}"}
    try:
        if agent_name == "auto_restock":
            return fn(user_id=user_id)
        return fn()
    except Exception as exc:
        logger.exception("run_agent(%s) failed: %s", agent_name, exc)
        return {"status": "error", "error": str(exc)}


def run_all_agents(user_id: int = 1) -> dict[str, Any]:
    """Run all AI agents. Called by /api/bot/run cron endpoint."""
    results = {}
    for name in AGENTS:
        logger.info("Running AI agent: %s", name)
        results[name] = run_agent(name, user_id=user_id)
    logger.info("AI agents complete: %s", results)
    return {"agents": results, "ran_at": datetime.now(timezone.utc).isoformat()}
