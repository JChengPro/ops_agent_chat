from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.action import Action, ToolInvocation
from app.models.evidence import RuntimeEvidence
from app.runtime.adapters.base import AdapterResult
from app.utils.redaction import redact_secrets


def record_result(db: Session, action: Action, executor_type: str, result: AdapterResult) -> RuntimeEvidence:
    now = datetime.now(timezone.utc)
    redacted_data = _redact_value(result.data)
    redacted_raw = redact_secrets(result.raw_output)
    redacted_error = redact_secrets(result.error)
    is_sensitive = redacted_data != result.data or redacted_raw != result.raw_output or redacted_error != result.error
    invocation = ToolInvocation(
        id=str(uuid4()),
        action_id=action.id,
        run_id=action.run_id,
        executor_type=executor_type,
        target_ref=str(action.target_json.get("name") or action.arguments_json.get("service") or ""),
        status=result.status,
        exit_code=result.exit_code,
        result_json=redacted_data,
        stdout_ref=redacted_raw[:65536] or None,
        stderr_ref=redacted_error[:16384] or None,
        finished_at=now,
        duration_ms=result.duration_ms,
    )
    db.add(invocation)
    db.flush()
    evidence = RuntimeEvidence(
        id=str(uuid4()),
        run_id=action.run_id,
        action_id=action.id,
        invocation_id=invocation.id,
        project_id=action.project_id,
        environment_id=action.environment_id,
        capability_name=action.capability_name,
        target_json=action.target_json,
        status=result.status,
        observed_at=now,
        fresh_until=now + timedelta(seconds=60) if action.effect == "read" and executor_type not in {"context", "experience"} else None,
        summary=result.summary,
        data_json=redacted_data,
        raw_output_ref=redacted_raw[:65536] or None,
        is_sensitive=is_sensitive,
        is_truncated=result.truncated,
    )
    db.add(evidence)
    action.status = "succeeded" if result.status == "success" else "failed"
    db.flush()
    return evidence


def _redact_value(value):
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value
