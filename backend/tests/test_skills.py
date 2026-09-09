from pathlib import Path

import pytest

from app.capabilities.registry import registry
from app.skills.registry import SkillRegistry, skill_registry


def test_builtin_skills_compile_and_only_narrow_capabilities():
    available = registry.resolve(
        "docker_compose",
        {"project.read", "runtime.read", "runtime.change"},
    )
    capabilities = [item.model_schema() for item in available]
    candidates = skill_registry.resolve("docker_compose", {item.name for item in available})
    assert {item.name for item in candidates} == {"runtime-diagnosis", "controlled-service-change"}

    diagnosis = skill_registry.get("runtime-diagnosis")
    narrowed = skill_registry.narrow_capabilities(diagnosis, capabilities)
    assert narrowed
    assert {item["name"] for item in narrowed}.issubset({item["name"] for item in capabilities})
    assert all(item["effect"] == "read" for item in narrowed)
    assert "service.restart" not in {item["name"] for item in narrowed}


def test_skill_is_ineligible_when_required_capability_is_unavailable():
    assert skill_registry.resolve("docker_compose", {"experience.search"}) == []


def test_skill_registry_rejects_unknown_capability(tmp_path: Path):
    directory = tmp_path / "bad"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        """---
name: bad-skill
version: 1.0.0
description: invalid fixture
runtimes: [docker_compose]
required_capabilities: [does.not.exist]
allowed_capabilities: [does.not.exist]
---
Do something invalid.
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown capabilities"):
        SkillRegistry(tmp_path)


def test_skill_definition_hash_changes_with_instructions(tmp_path: Path):
    directory = tmp_path / "skill"
    directory.mkdir()
    path = directory / "SKILL.md"
    prefix = """---
name: test-skill
version: 1.0.0
description: valid fixture
runtimes: [docker_compose]
required_capabilities: [service.status]
allowed_capabilities: [service.status]
---
"""
    path.write_text(prefix + "First instruction.\n", encoding="utf-8")
    first = SkillRegistry(tmp_path).get("test-skill")
    path.write_text(prefix + "Second instruction.\n", encoding="utf-8")
    second = SkillRegistry(tmp_path).get("test-skill")
    assert first.definition_hash != second.definition_hash
