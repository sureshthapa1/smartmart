"""AI Layer 2: Auto Learning & Model Retraining Engine

Implements:
- Scheduled model retraining
- Dynamic threshold adjustment
- Model versioning and rollback
- Accuracy tracking
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from statistics import mean, stdev

from sqlalchemy import func

from ..extensions import db
from ..models.product import Product
from ..models.sale import Sale, SaleItem
from ..models.ai_memory import AIModelVersion, AITrainingLog


def _get_daily_sales_series(product_id: int, days: int = 90) -> list[float]:
    end = date.today()
    start = end - timedelta(days=days - 1)
    rows = db.session.execute(
        db.select(
            func.date(Sale.sale_date).label("day"),
            func.sum(SaleItem.quantity).label("qty"),
        )
        .join(SaleItem, SaleItem.sale_id == Sale.id)
        .where(SaleItem.product_id == product_id)
        .where(func.date(Sale.sale_date) >= start)
        .group_by(func.date(Sale.sale_date))
    ).all()
    sales_map = {str(r.day): float(r.qty) for r in rows}
    series = []
    current = start
    while current <= end:
        series.append(sales_map.get(str(current), 0.0))
        current += timedelta(days=1)
    return series


def _linear_regression(series: list[float]) -> tuple[float, float]:
    n = len(series)
    if n < 2:
        return 0.0, series[0] if series else 0.0
    x_mean = (n - 1) / 2
    y_mean = sum(series) / n
    num = sum((i - x_mean) * (series[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    slope = num / den if den else 0.0
    return slope, y_mean - slope * x_mean


def retrain_demand_model(product_id: int = None, trigger: str = "manual") -> dict:
    """Retrain demand forecasting model for one or all products."""
    log = AITrainingLog(
        model_name="demand_forecast",
        trigger=trigger,
        started_at=datetime.now(timezone.utc),
        status="running",
    )
    db.session.add(log)
    db.session.commit()

    try:
        products = [db.session.get(Product, product_id)] if product_id else \
            db.session.execute(db.select(Product)).scalars().all()

        trained = 0
        total_accuracy = 0.0

        for p in products:
            if not p:
                continue
            series = _get_daily_sales_series(p.id, days=90)
            if sum(series) == 0:
                continue

            slope, intercept = _linear_regression(series)
            ma7 = mean(series[-7:]) if len(series) >= 7 else mean(series)
            ma30 = mean(series[-30:]) if len(series) >= 30 else mean(series)

            # Calculate accuracy: compare last 7 days predicted vs actual
            if len(series) >= 14:
                train = series[:-7]
                actual = series[-7:]
                s2, i2 = _linear_regression(train)
                predicted = [max(0, i2 + s2 * (len(train) + j)) for j in range(7)]
                mae = mean(abs(a - p2) for a, p2 in zip(actual, predicted))
                avg_actual = mean(actual) if mean(actual) > 0 else 1
                accuracy = max(0.0, 1.0 - (mae / avg_actual))
            else:
                accuracy = 0.5

            params = json.dumps({
                "slope": round(slope, 6),
                "intercept": round(intercept, 4),
                "ma7": round(ma7, 4),
                "ma30": round(ma30, 4),
                "product_id": p.id,
            })

            # Deactivate old versions
            db.session.execute(
                db.update(AIModelVersion)
                .where(AIModelVersion.model_name == f"demand_{p.id}")
                .values(is_active=False)
            )

            # Get next version number
            last = db.session.execute(
                db.select(func.max(AIModelVersion.version))
                .where(AIModelVersion.model_name == f"demand_{p.id}")
            ).scalar() or 0

            new_version = AIModelVersion(
                model_name=f"demand_{p.id}",
                version=last + 1,
                accuracy_score=round(accuracy, 4),
                parameters=params,
                training_samples=len([x for x in series if x > 0]),
                is_active=True,
            )
            db.session.add(new_version)
            trained += 1
            total_accuracy += accuracy

        avg_accuracy = total_accuracy / trained if trained else 0

        log.status = "completed"
        log.completed_at = datetime.now(timezone.utc)
        log.samples_used = trained
        log.new_accuracy = round(avg_accuracy, 4)
        db.session.commit()

        return {
            "status": "completed",
            "products_trained": trained,
            "avg_accuracy": round(avg_accuracy, 4),
            "trigger": trigger,
            "log_id": log.id,
        }

    except Exception as e:
        log.status = "failed"
        log.error_message = str(e)
        db.session.commit()
        return {"status": "failed", "error": str(e)}


def retrain_anomaly_thresholds(trigger: str = "scheduled") -> dict:
    """Dynamically recalculate anomaly detection thresholds from recent data."""
    log = AITrainingLog(
        model_name="anomaly_thresholds",
        trigger=trigger,
        started_at=datetime.now(timezone.utc),
        status="running",
    )
    db.session.add(log)
    db.session.commit()

    try:
        # Calculate dynamic thresholds from last 60 days
        end = date.today()
        start = end - timedelta(days=60)

        daily_rows = db.session.execute(
            db.select(
                func.date(Sale.sale_date).label("day"),
                func.sum(Sale.total_amount).label("total"),
            )
            .where(func.date(Sale.sale_date) >= start)
            .group_by(func.date(Sale.sale_date))
        ).all()

        if len(daily_rows) >= 7:
            totals = [float(r.total) for r in daily_rows]
            avg = mean(totals)
            sd = stdev(totals) if len(totals) > 1 else avg * 0.3
            spike_threshold = avg + 2 * sd
            drop_threshold = max(0, avg - 2 * sd)
        else:
            avg = sd = spike_threshold = drop_threshold = 0

        params = json.dumps({
            "avg_daily_sales": round(avg, 2),
            "std_daily_sales": round(sd, 2),
            "spike_threshold": round(spike_threshold, 2),
            "drop_threshold": round(drop_threshold, 2),
            "calculated_from_days": len(daily_rows),
        })

        db.session.execute(
            db.update(AIModelVersion)
            .where(AIModelVersion.model_name == "anomaly_thresholds")
            .values(is_active=False)
        )
        last = db.session.execute(
            db.select(func.max(AIModelVersion.version))
            .where(AIModelVersion.model_name == "anomaly_thresholds")
        ).scalar() or 0

        db.session.add(AIModelVersion(
            model_name="anomaly_thresholds",
            version=last + 1,
            parameters=params,
            training_samples=len(daily_rows),
            is_active=True,
        ))

        log.status = "completed"
        log.completed_at = datetime.now(timezone.utc)
        log.samples_used = len(daily_rows)
        db.session.commit()

        return {"status": "completed", "thresholds": json.loads(params)}

    except Exception as e:
        log.status = "failed"
        log.error_message = str(e)
        db.session.commit()
        return {"status": "failed", "error": str(e)}


def get_model_version(model_name: str) -> dict | None:
    """Get the active model version and its parameters."""
    v = db.session.execute(
        db.select(AIModelVersion)
        .where(AIModelVersion.model_name == model_name)
        .where(AIModelVersion.is_active == True)
        .order_by(AIModelVersion.version.desc())
    ).scalar_one_or_none()
    if not v:
        return None
    return {
        "model_name": v.model_name,
        "version": v.version,
        "accuracy": v.accuracy_score,
        "trained_at": str(v.trained_at),
        "parameters": json.loads(v.parameters) if v.parameters else {},
        "samples": v.training_samples,
    }


def rollback_model(model_name: str) -> dict:
    """Rollback to the previous model version."""
    versions = db.session.execute(
        db.select(AIModelVersion)
        .where(AIModelVersion.model_name == model_name)
        .order_by(AIModelVersion.version.desc())
        .limit(2)
    ).scalars().all()

    if len(versions) < 2:
        return {"status": "error", "message": "No previous version to rollback to."}

    current, previous = versions[0], versions[1]
    current.is_active = False
    previous.is_active = True
    db.session.commit()

    return {
        "status": "rolled_back",
        "from_version": current.version,
        "to_version": previous.version,
        "model": model_name,
    }


def get_training_history(limit: int = 20) -> list[dict]:
    """Get recent training log entries."""
    logs = db.session.execute(
        db.select(AITrainingLog).order_by(AITrainingLog.started_at.desc()).limit(limit)
    ).scalars().all()
    return [{
        "id": l.id,
        "model": l.model_name,
        "trigger": l.trigger,
        "status": l.status,
        "started": str(l.started_at),
        "completed": str(l.completed_at) if l.completed_at else None,
        "samples": l.samples_used,
        "accuracy": l.new_accuracy,
        "improvement": l.improvement,
    } for l in logs]


def run_full_retraining(trigger: str = "scheduled") -> dict:
    """Run complete retraining of all models."""
    results = {
        "demand_forecast": retrain_demand_model(trigger=trigger),
        "anomaly_thresholds": retrain_anomaly_thresholds(trigger=trigger),
        "triggered_at": str(datetime.now(timezone.utc)),
        "trigger": trigger,
    }
    return results
