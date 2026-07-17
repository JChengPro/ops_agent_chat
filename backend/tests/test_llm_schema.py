import pytest
import json
from pydantic import ValidationError

from app.llm.gateway import _bounded_items, _normalize_decision_payload
from app.llm.schemas import AgentDecision


def request(**overrides):
    value = {"goal": "explain", "scope": "general", "time_focus": "timeless", "requested_effect": "none", "subjects": [], "desired_output": "answer", "constraints": [], "confidence": 0.95, "summary": "Explain consequences"}
    value.update(overrides)
    return value


def test_general_question_about_destructive_operation_is_direct_answer():
    result = AgentDecision.model_validate({"decision": "respond", "request": request(), "tool_calls": [], "answer": "Deleting a container stops its process and removes writable container state.", "clarification_question": None})
    assert result.request.requested_effect == "none"
    assert result.decision == "respond"


def test_change_plan_must_be_explicitly_marked_as_change():
    with pytest.raises(ValidationError):
        AgentDecision.model_validate({"decision": "propose_change", "request": request(), "tool_calls": [{"capability": "service.restart", "arguments": {"service": "redis"}}]})


def test_change_effect_cannot_hide_in_read_tool_decision():
    with pytest.raises(ValidationError):
        AgentDecision.model_validate({"decision": "invoke_tools", "request": request(requested_effect="change"), "tool_calls": [{"capability": "service.restart", "arguments": {"service": "redis"}}]})


def test_direct_response_cannot_smuggle_tool_calls():
    with pytest.raises(ValidationError):
        AgentDecision.model_validate({"decision": "respond", "request": request(), "tool_calls": [{"capability": "service.restart", "arguments": {"service": "redis"}}], "answer": "done"})


def test_model_context_items_are_bounded():
    result = _bounded_items([{"data": "x" * 5000}, {"data": "y" * 5000}], budget=1200, item_limit=1000)
    assert len(json.dumps(result)) < 1300
    assert result[-1]["truncated"] is True


def test_structured_change_mode_is_normalized_without_keywords():
    payload = {"decision": "invoke_tools", "request": request(requested_effect="change"), "tool_calls": [{"capability": "service.restart", "arguments": {"service": "redis"}}]}
    normalized = _normalize_decision_payload(payload)
    assert normalized["decision"] == "propose_change"
    assert AgentDecision.model_validate(normalized).request.requested_effect == "change"


def test_empty_tool_decision_preserves_model_clarification():
    payload = {
        "decision": "propose_change",
        "request": request(requested_effect="change"),
        "tool_calls": [],
        "answer": None,
        "clarification_question": "Do you want to restart every service?",
    }
    normalized = _normalize_decision_payload(payload)
    decision = AgentDecision.model_validate(normalized)
    assert decision.decision == "clarify"
    assert decision.clarification_question == "Do you want to restart every service?"


def test_empty_tool_decision_has_safe_clarification_fallback():
    payload = {
        "decision": "propose_change",
        "request": request(requested_effect="change"),
        "tool_calls": [],
        "answer": None,
        "clarification_question": None,
    }
    decision = AgentDecision.model_validate(_normalize_decision_payload(payload))
    assert decision.decision == "clarify"
    assert "具体操作目标" in decision.clarification_question


def test_tool_decision_supports_the_run_level_tool_budget():
    tool_calls = [
        {"capability": "service.status", "arguments": {"service": f"service-{index}"}, "purpose": "检查服务状态"}
        for index in range(50)
    ]
    decision = AgentDecision.model_validate({
        "decision": "invoke_tools",
        "request": request(goal="investigate", scope="runtime", time_focus="current", requested_effect="read"),
        "tool_calls": tool_calls,
    })
    assert len(decision.tool_calls) == 50


def test_non_response_claims_are_removed_during_normalization():
    payload = {
        "decision": "propose_change",
        "request": request(goal="change", scope="runtime", time_focus="current", requested_effect="none"),
        "tool_calls": [{"capability": "service.start", "arguments": {"service": "backend"}}],
        "claims": [{"text": "尚未执行", "claim_type": "gap", "confidence": 0.5}],
    }
    normalized = _normalize_decision_payload(payload)
    decision = AgentDecision.model_validate(normalized)
    assert decision.request.requested_effect == "change"
    assert decision.claims == []
