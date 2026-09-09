from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    version: str
    description: str
    runtimes: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    allowed_capabilities: tuple[str, ...]
    instructions: str
    definition_hash: str

    def selector_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "runtimes": list(self.runtimes),
        }

    def trace(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "definition_hash": self.definition_hash,
        }


class SkillSelection(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    confidence: float = Field(default=0.0, ge=0, le=1)
    reason: str = Field(default="", max_length=1000)
