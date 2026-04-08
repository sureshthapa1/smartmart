import json
from decimal import Decimal

from ..extensions import db
from ..models.ai_enhancements import AIDecisionLog


def _json_safe(value):
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Not JSON serializable: {type(value)}")


def log_decision(
    decision_type: str,
    entity_type: str,
    entity_id: str | int | None,
    input_snapshot: dict,
    output_snapshot: dict,
    confidence: float | None = None,
):
    db.session.add(
        AIDecisionLog(
            decision_type=decision_type,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            input_snapshot=json.dumps(input_snapshot, default=_json_safe),
            output_snapshot=json.dumps(output_snapshot, default=_json_safe),
            confidence=confidence,
        )
    )
