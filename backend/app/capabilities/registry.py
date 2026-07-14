from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.capabilities.schemas import CapabilityDefinition
from app.models.action import CapabilityVersion


class CapabilityRegistry:
    def __init__(self, definitions_path: Path | None = None) -> None:
        root = definitions_path or Path(__file__).parent / "definitions"
        self._definitions: dict[str, CapabilityDefinition] = {}
        for path in sorted(root.glob("*.yml")):
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or []
            for raw in payload:
                definition = CapabilityDefinition(
                    name=raw["name"],
                    description=raw["description"],
                    effect=raw["effect"],
                    risk_level=raw["risk_level"],
                    runtimes=tuple(raw["runtimes"]),
                    permission=raw["permission"],
                    approval_mode=raw["approval_mode"],
                    executor=raw["executor"],
                    arguments=raw.get("arguments") or {},
                    version=str(raw.get("version", "final-1")),
                    precheck=raw.get("precheck"),
                    verifier=raw.get("verifier"),
                    rollback=raw.get("rollback"),
                )
                if definition.name in self._definitions:
                    raise ValueError(f"Duplicate capability: {definition.name}")
                self._definitions[definition.name] = definition

    def get(self, name: str) -> CapabilityDefinition | None:
        return self._definitions.get(name)

    def resolve(self, runtime_type: str | None, permissions: set[str]) -> list[CapabilityDefinition]:
        if not runtime_type:
            return []
        return [
            item
            for item in self._definitions.values()
            if runtime_type in item.runtimes and item.permission in permissions
        ]

    def sync_versions(self, db: Session) -> None:
        for definition in self._definitions.values():
            canonical = json.dumps(asdict(definition), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(canonical.encode()).hexdigest()
            row = db.scalar(
                select(CapabilityVersion).where(
                    CapabilityVersion.name == definition.name,
                    CapabilityVersion.version == definition.version,
                )
            )
            if row:
                if row.definition_hash != digest:
                    raise ValueError(f"Capability {definition.name}@{definition.version} changed without a version bump")
                row.enabled = True
                continue
            db.add(
                CapabilityVersion(
                    name=definition.name,
                    version=definition.version,
                    definition_hash=digest,
                    effect=definition.effect,
                    default_risk_level=definition.risk_level,
                    approval_mode=definition.approval_mode,
                    enabled=True,
                )
            )


registry = CapabilityRegistry()
