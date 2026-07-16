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
    EXECUTORS = {"context", "experience", "runtime", "registered_deployment", "registered_config"}
    RUNTIMES = {"manual", "docker_compose", "kubernetes", "systemd", "mixed"}

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
        self._compile()

    def _compile(self) -> None:
        for definition in self._definitions.values():
            if definition.executor not in self.EXECUTORS:
                raise ValueError(f"Capability {definition.name} uses unknown executor: {definition.executor}")
            if definition.effect not in {"read", "change"}:
                raise ValueError(f"Capability {definition.name} has invalid effect: {definition.effect}")
            if definition.risk_level not in {"L0", "L1", "L2", "L3"}:
                raise ValueError(f"Capability {definition.name} has invalid risk level: {definition.risk_level}")
            if definition.approval_mode not in {"never", "always", "conditional", "forbidden"}:
                raise ValueError(f"Capability {definition.name} has invalid approval mode: {definition.approval_mode}")
            if not definition.runtimes or set(definition.runtimes) - self.RUNTIMES:
                raise ValueError(f"Capability {definition.name} has invalid runtimes: {definition.runtimes}")
            if definition.effect == "read" and definition.approval_mode != "never":
                raise ValueError(f"Read capability {definition.name} cannot require approval")
            if definition.effect == "change" and definition.approval_mode not in {"always", "conditional", "forbidden"}:
                raise ValueError(f"Change capability {definition.name} must define an explicit approval policy")
            if definition.effect == "change" and (not definition.precheck or not definition.verifier):
                raise ValueError(f"Change capability {definition.name} requires precheck and verifier")
            for relation, referenced_name in (
                ("precheck", definition.precheck),
                ("verifier", definition.verifier),
                ("rollback", definition.rollback),
            ):
                if referenced_name and referenced_name not in self._definitions:
                    raise ValueError(f"Capability {definition.name} references unknown {relation}: {referenced_name}")
            if definition.precheck and self._definitions[definition.precheck].effect != "read":
                raise ValueError(f"Capability {definition.name} precheck must be read-only")
            if definition.verifier and self._definitions[definition.verifier].effect != "read":
                raise ValueError(f"Capability {definition.name} verifier must be read-only")
            if definition.rollback and self._definitions[definition.rollback].effect != "change":
                raise ValueError(f"Capability {definition.name} rollback must be state-changing")
            for relation in (definition.precheck, definition.verifier, definition.rollback):
                if relation and not set(self._definitions[relation].arguments).issubset(definition.arguments):
                    raise ValueError(f"Capability {definition.name} cannot supply all arguments required by {relation}")
                if relation and not set(definition.runtimes).issubset(self._definitions[relation].runtimes):
                    raise ValueError(f"Capability {definition.name} relation {relation} does not support all required runtimes")

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
