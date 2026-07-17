import json
from typing import Any


STOPPED_STATES = {"exited", "stopped", "dead", "created"}


def parse_json_records(raw: object) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(raw, str) or not raw.strip():
        return [], False
    text = raw.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        records: list[dict[str, Any]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                return [], False
            if not isinstance(item, dict):
                return [], False
            records.append(item)
        return records, bool(records)
    if isinstance(parsed, dict):
        return [parsed], True
    if isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
        return parsed, True
    return [], False


def docker_status_data(raw: object) -> dict[str, Any]:
    records, valid = parse_json_records(raw)
    running = [item for item in records if _state(item) == "running"]
    health_values = [_health(item) for item in records if _has_health(item)]
    return {
        "records": records,
        "parse_valid": valid,
        "instance_count": len(records),
        "running_count": len(running),
        "healthy_count": len([value for value in health_values if value == "healthy"]),
        "healthcheck_count": len(health_values),
        "exit_codes": [_exit_code(item) for item in records if _has_exit_code(item)],
    }


def verification_satisfied(action: Any, observation: dict[str, Any]) -> bool:
    if observation.get("status") != "success":
        return False
    data = observation.get("data") or {}
    runtime_type = str((action.resolved_spec_json or {}).get("runtime_type") or "")
    if action.capability_name in {"service.start", "service.restart", "service.stop", "service.scale"}:
        if runtime_type == "systemd" or _looks_like_systemd(data):
            return _verify_systemd(action, data)
        if runtime_type == "kubernetes" or _looks_like_kubernetes(data):
            return _verify_kubernetes(action, data)
        return _verify_docker(action, data)
    if action.capability_name == "deployment.apply_registered":
        if data.get("deployment_ready") is True:
            return True
        return _verify_registered_deployment(data)
    if action.capability_name == "config.update_registered":
        expected = data.get("expected_sha256")
        actual = data.get("actual_sha256")
        return bool(expected) and expected == actual
    return False


def runtime_records(data: dict[str, Any]) -> list[dict[str, Any]]:
    records = data.get("records")
    if isinstance(records, list) and all(isinstance(item, dict) for item in records):
        return records
    parsed, valid = parse_json_records(data.get("stdout"))
    return parsed if valid else []


def _verify_docker(action: Any, data: dict[str, Any]) -> bool:
    records = data.get("records")
    valid = data.get("parse_valid")
    if not isinstance(records, list) or valid is not True:
        records, valid = parse_json_records(data.get("stdout"))
    if not valid:
        return False
    expected = action.arguments_json.get("replicas") if action.capability_name == "service.scale" else None
    if action.capability_name == "service.scale" and expected == 0:
        return len(records) == 0
    if not records:
        return False
    if action.capability_name == "service.stop":
        return all(_state(item) in STOPPED_STATES for item in records)
    if expected is not None and len(records) != expected:
        return False
    return all(
        _state(item) == "running"
        and (not _has_health(item) or _health(item) == "healthy")
        and (not _has_exit_code(item) or _exit_code(item) == 0)
        for item in records
    )


def _verify_kubernetes(action: Any, data: dict[str, Any]) -> bool:
    records = runtime_records(data)
    if len(records) != 1:
        return False
    deployment = records[0]
    spec = deployment.get("spec")
    status = deployment.get("status")
    if not isinstance(spec, dict) or not isinstance(status, dict):
        return False
    desired = _int(spec.get("replicas"), -1)
    available = _int(status.get("availableReplicas"), 0)
    updated = _int(status.get("updatedReplicas"), available)
    unavailable = _int(status.get("unavailableReplicas"), 0)
    expected = action.arguments_json.get("replicas") if action.capability_name == "service.scale" else None
    if action.capability_name == "service.stop":
        return desired == 0 and available == 0 and updated == 0
    if expected is not None and desired != expected:
        return False
    if action.capability_name in {"service.start", "service.restart"} and desired <= 0:
        return False
    return available == desired and updated == desired and unavailable == 0


def _verify_systemd(action: Any, data: dict[str, Any]) -> bool:
    values = _systemd_values(data)
    if not values or values.get("LoadState", "loaded") != "loaded":
        return False
    active = values.get("ActiveState", "").lower()
    sub = values.get("SubState", "").lower()
    if action.capability_name == "service.stop":
        return active == "inactive" and sub in {"dead", "exited"}
    return active == "active" and sub in {"running", "listening", "exited"}


def _verify_registered_deployment(data: dict[str, Any]) -> bool:
    records = runtime_records(data)
    expected = _int(data.get("expected_instances"), 1)
    return len(records) == expected and expected > 0 and all(
        _state(item) == "running"
        and (not _has_health(item) or _health(item) == "healthy")
        and (not _has_exit_code(item) or _exit_code(item) == 0)
        for item in records
    )


def _looks_like_kubernetes(data: dict[str, Any]) -> bool:
    records = runtime_records(data)
    return bool(records and isinstance(records[0].get("spec"), dict) and isinstance(records[0].get("status"), dict))


def _looks_like_systemd(data: dict[str, Any]) -> bool:
    return "activestate=" in str(data.get("stdout") or "").lower()


def _systemd_values(data: dict[str, Any]) -> dict[str, str]:
    raw = data.get("stdout")
    if not isinstance(raw, str) or not raw.strip():
        return {}
    values: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" not in line:
            return {}
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _state(item: dict[str, Any]) -> str:
    return str(item.get("State") or item.get("state") or "").lower()


def _has_health(item: dict[str, Any]) -> bool:
    value = item.get("Health") if "Health" in item else item.get("health")
    return value is not None and bool(str(value).strip())


def _health(item: dict[str, Any]) -> str:
    return str(item.get("Health") if "Health" in item else item.get("health") or "").lower()


def _has_exit_code(item: dict[str, Any]) -> bool:
    return "ExitCode" in item or "exit_code" in item


def _exit_code(item: dict[str, Any]) -> int:
    return _int(item.get("ExitCode") if "ExitCode" in item else item.get("exit_code"), -1)


def _int(value: object, default: int) -> int:
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
