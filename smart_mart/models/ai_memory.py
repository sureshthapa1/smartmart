"""AI Data Memory Layer — stores AI decisions, model versions, feedback, and alerts."""
from datetime import datetime, timezone
from ..extensions import db


class AIModelVersion(db.Model):
    """Tracks model versions and retraining history."""
    __tablename__ = "ai_model_versions"
    id = db.Column(db.Integer, primary_key=True)
    model_name = db.Column(db.String(80), nullable=False)  # demand_forecast, anomaly, etc.
    version = db.Column(db.Integer, nullable=False, default=1)
    trained_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    accuracy_score = db.Column(db.Float, nullable=True)
    data_points_used = db.Column(db.Integer, default=0)
    parameters = db.Column(db.Text, nullable=True)  # JSON string of model params
    is_active = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text, nullable=True)


class AIAlert(db.Model):
    """Self-detected issues and alerts."""
    __tablename__ = "ai_alerts"
    id = db.Column(db.Integer, primary_key=True)
    alert_type = db.Column(db.String(50), nullable=False)
    severity = db.Column(db.String(10), nullable=False)  # low/medium/high/critical
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    entity_type = db.Column(db.String(50), nullable=True)  # product/supplier/sale
    entity_id = db.Column(db.Integer, nullable=True)
    is_resolved = db.Column(db.Boolean, default=False)
    resolved_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class AIRecommendation(db.Model):
    """AI-generated recommendations with feedback tracking."""
    __tablename__ = "ai_recommendations"
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False)  # pricing/restock/supplier/discount
    title = db.Column(db.String(200), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    expected_impact = db.Column(db.String(200), nullable=True)
    confidence_score = db.Column(db.Float, default=0.5)  # 0.0 - 1.0
    entity_type = db.Column(db.String(50), nullable=True)
    entity_id = db.Column(db.Integer, nullable=True)
    action_data = db.Column(db.Text, nullable=True)  # JSON
    status = db.Column(db.String(20), default="pending")  # pending/accepted/rejected/modified
    feedback_note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    acted_at = db.Column(db.DateTime, nullable=True)


class AIFeedbackLog(db.Model):
    """Tracks feedback on AI recommendations for learning loop."""
    __tablename__ = "ai_feedback_log"
    id = db.Column(db.Integer, primary_key=True)
    recommendation_id = db.Column(db.Integer, db.ForeignKey("ai_recommendations.id"))
    action = db.Column(db.String(20), nullable=False)  # accepted/rejected/modified
    outcome = db.Column(db.String(50), nullable=True)  # positive/negative/neutral
    notes = db.Column(db.Text, nullable=True)
    logged_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class AIRetrainingLog(db.Model):
    """Logs of scheduled retraining runs."""
    __tablename__ = "ai_retraining_log"
    id = db.Column(db.Integer, primary_key=True)
    trigger = db.Column(db.String(50), nullable=False)  # scheduled/manual/drift_detected
    models_retrained = db.Column(db.Text, nullable=True)  # JSON list
    started_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default="running")  # running/completed/failed
    summary = db.Column(db.Text, nullable=True)
