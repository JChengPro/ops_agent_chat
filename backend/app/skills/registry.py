from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from app.capabilities.registry import CapabilityRegistry, registry as capability_registry
from app.skills.schemas import SkillDefinition


class SkillRegistry:
    def __init__(
        self,
        definitions_path: Path | None = None,
        *,
        capabilities: CapabilityRegistry | None = None,
    ) -> None:
        root = definitions_path or Path(__file__).parent / "definitions"
        self.capabilities = capabilities or capability_registry
        self._definitions: dict[str, SkillDefinition] = {}
        for path in sorted(root.glob("*/SKILL.md")):
            definition = self._load(path)
            if definition.name in self._definitions:
                raise ValueError(f"Duplicate skill: {definition.name}")
            self._definitions[definition.name] = definition
        self._compile()

    @staticmethod
    def _load(path: Path) -> SkillDefinition:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            raise ValueError(f"Skill {path} must start with YAML front matter")
        try:
            _, raw_metadata, instructions = text.split("---", 2)
        except ValueError as exc:
            raise ValueError(f"Skill {path} has incomplete YAML front matter") from exc
        metadata = yaml.safe_load(raw_metadata) or {}
        canonical = json.dumps(
            {"metadata": metadata, "instructions": instructions.strip()},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return SkillDefinition(
            name=str(metadata.get("name") or "").strip(),
            version=str(metadata.get("version") or "").strip(),
            description=str(metadata.get("description") or "").strip(),
            runtimes=tuple(metadata.get("runtimes") or ()),
            required_capabilities=tuple(metadata.get("required_capabilities") or ()),
            allowed_capabilities=tuple(metadata.get("allowed_capabilities") or ()),
            instructions=instructions.strip(),
            definition_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        )

    def _compile(self) -> None:
        known_capabilities = set(self.capabilities.names())
        known_runtimes = self.capabilities.RUNTIMES
        for skill in self._definitions.values():
            if not skill.name or not skill.version or not skill.description or not skill.instructions:
                raise ValueError("Skill name, version, description and instructions are required")
            if not skill.runtimes or set(skill.runtimes) - known_runtimes:
                raise ValueError(f"Skill {skill.name} has invalid runtimes: {skill.runtimes}")
            required = set(skill.required_capabilities)
            allowed = set(skill.allowed_capabilities)
            unknown = (required | allowed) - known_capabilities
            if unknown:
                raise ValueError(f"Skill {skill.name} references unknown capabilities: {', '.join(sorted(unknown))}")
            if not required:
                raise ValueError(f"Skill {skill.name} must declare at least one required capability")
            if not allowed or not required.issubset(allowed):
                raise ValueError(f"Skill {skill.name} required capabilities must be included in allowed_capabilities")

    def names(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def get(self, name: str) -> SkillDefinition | None:
        return self._definitions.get(name)

    def resolve(self, runtime_type: str | None, available_capability_names: set[str]) -> list[SkillDefinition]:
        if not runtime_type:
            return []
        return [
            skill
            for skill in self._definitions.values()
            if runtime_type in skill.runtimes
            and set(skill.required_capabilities).issubset(available_capability_names)
            and bool(set(skill.allowed_capabilities) & available_capability_names)
        ]

    @staticmethod
    def narrow_capabilities(skill: SkillDefinition, capabilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        allowed = set(skill.allowed_capabilities)
        return [capability for capability in capabilities if str(capability.get("name")) in allowed]


skill_registry = SkillRegistry()
