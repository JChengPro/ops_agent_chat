from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


class CapabilityValidationError(ValueError):
    pass


@dataclass(frozen=True)
class CapabilityDefinition:
    name: str
    description: str
    effect: str
    risk_level: str
    runtimes: tuple[str, ...]
    permission: str
    approval_mode: str
    executor: str
    arguments: dict[str, dict[str, Any]] = field(default_factory=dict)
    version: str = "final-1"
    precheck: str | None = None
    verifier: str | None = None
    rollback: str | None = None

    def validate_arguments(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise CapabilityValidationError("Capability arguments must be an object")
        unknown = set(raw) - set(self.arguments)
        if unknown:
            raise CapabilityValidationError(f"Unsupported arguments: {', '.join(sorted(unknown))}")
        result: dict[str, Any] = {}
        for name, schema in self.arguments.items():
            value = raw.get(name, schema.get("default"))
            if value is None:
                if schema.get("required"):
                    raise CapabilityValidationError(f"Missing required argument: {name}")
                continue
            expected = schema.get("type")
            if expected == "string":
                if not isinstance(value, str) or not value.strip():
                    raise CapabilityValidationError(f"{name} must be a non-empty string")
                value = value.strip()
                if len(value) > int(schema.get("max_length", 1000)):
                    raise CapabilityValidationError(f"{name} is too long")
                if name in {"service", "deployment", "change"} and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,254}", value):
                    raise CapabilityValidationError(f"{name} contains unsupported characters")
            elif expected == "integer":
                if isinstance(value, bool) or not isinstance(value, int):
                    raise CapabilityValidationError(f"{name} must be an integer")
                if value < int(schema.get("minimum", value)) or value > int(schema.get("maximum", value)):
                    raise CapabilityValidationError(f"{name} is outside the allowed range")
            else:
                raise CapabilityValidationError(f"Unsupported schema type for {name}")
            result[name] = value
        return result

    def model_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "effect": self.effect,
            "risk_level": self.risk_level,
            "arguments": self.arguments,
        }
