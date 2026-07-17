from dataclasses import replace

import pytest

from app.capabilities.registry import registry
from app.capabilities.schemas import CapabilityValidationError
from app.policy.action_hash import compute_action_hash
from app.agent.graph import OpsAgentGraph
from app.models.action import Action


def test_registry_has_semantic_capabilities_and_no_free_shell():
    assert registry.get("service.status") is not None
    assert registry.get("service.restart") is not None
    assert registry.get("container.delete") is None
    assert registry.get("run_any_shell") is None


def test_capability_schema_rejects_unknown_and_out_of_range_arguments():
    logs = registry.get("service.logs")
    assert logs.validate_arguments({"service": "redis"}) == {"service": "redis", "tail": 100}
    try:
        logs.validate_arguments({"service": "redis", "tail": 1001})
        raise AssertionError("expected validation error")
    except CapabilityValidationError:
        pass
    try:
        logs.validate_arguments({"service": "redis", "command": "rm -rf /"})
        raise AssertionError("expected validation error")
    except CapabilityValidationError:
        pass
    try:
        logs.validate_arguments({"service": "--help"})
        raise AssertionError("expected validation error")
    except CapabilityValidationError:
        pass


def test_action_hash_is_stable_and_parameter_bound():
    action = {"capability": "service.restart", "version": "final-1", "definition_hash": "a" * 64, "project_id": 1, "arguments": {"service": "redis"}}
    assert compute_action_hash(action) == compute_action_hash(dict(reversed(list(action.items()))))
    changed = {**action, "arguments": {"service": "mysql"}}
    assert compute_action_hash(action) != compute_action_hash(changed)
    changed_definition = {**action, "definition_hash": "b" * 64}
    assert compute_action_hash(action) != compute_action_hash(changed_definition)


def test_registry_binding_requires_exact_name_version_and_definition_hash(monkeypatch):
    definition = registry.get("service.restart")
    binding = registry.binding(definition)
    assert registry.get_bound(**binding) == definition
    assert registry.get_bound(definition.name, "other-version", binding["definition_hash"]) is None
    assert registry.get_bound(definition.name, definition.version, "0" * 64) is None

    changed_without_version_bump = replace(definition, description="silently changed definition")
    monkeypatch.setitem(registry._definitions, definition.name, changed_without_version_bump)
    assert registry.get_bound(**binding) is None


def test_scale_verification_counts_compose_instances_and_kubernetes_replicas():
    compose_action = Action(capability_name="service.scale", arguments_json={"replicas": 2})
    compose = {"status": "success", "data": {"stdout": '{"State":"running"}\n{"State":"running"}\n'}}
    assert OpsAgentGraph._verification_satisfied(compose_action, compose)
    compose_action.arguments_json = {"replicas": 1}
    assert not OpsAgentGraph._verification_satisfied(compose_action, compose)
    kubernetes_action = Action(capability_name="service.scale", arguments_json={"replicas": 3})
    kubernetes = {"status": "success", "data": {"stdout": '{"spec":{"replicas":3},"status":{"availableReplicas":3}}'}}
    assert OpsAgentGraph._verification_satisfied(kubernetes_action, kubernetes)


def test_restart_precheck_rejects_empty_status_output():
    action = Action(capability_name="service.restart", arguments_json={"service": "redis"})
    assert not OpsAgentGraph._precheck_satisfied(action, {"status": "success", "data": {"stdout": ""}})


@pytest.mark.parametrize("capability", ["service.start", "service.stop", "service.restart"])
def test_state_change_precheck_rejects_unobserved_service(capability):
    action = Action(capability_name=capability, arguments_json={"service": "redis"})
    assert not OpsAgentGraph._precheck_satisfied(action, {"status": "success", "data": {"stdout": "[]", "records": [], "parse_valid": True}})


def test_scale_precheck_accepts_verified_zero_instance_state():
    action = Action(capability_name="service.scale", arguments_json={"service": "redis", "replicas": 1})
    assert OpsAgentGraph._precheck_satisfied(action, {"status": "success", "data": {"stdout": "[]", "records": [], "parse_valid": True}})
