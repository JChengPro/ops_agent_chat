from typing import Any


SENSITIVE_KEYS = {
    "access_key",
    "api_key",
    "content",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
}


def public_config(value: Any) -> Any:
    """Return configuration metadata without exposing secret or file payload values."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = key.lower().replace("-", "_")
            if normalized in SENSITIVE_KEYS or any(normalized.endswith(f"_{suffix}") for suffix in SENSITIVE_KEYS):
                result[f"{key}_configured"] = item not in (None, "", [], {})
            else:
                result[key] = public_config(item)
        return result
    if isinstance(value, list):
        return [public_config(item) for item in value]
    return value
