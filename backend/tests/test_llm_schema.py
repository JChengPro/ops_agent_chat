import pytest
import json
from pydantic import ValidationError

from app.llm.gateway import _bounded_items, _fallback_request_plan, _normalize_decision_payload
from app.llm.schemas import AgentDecision, GeneralChatResponse, RequestUnderstanding


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


def test_tool_decision_does_not_impose_an_arbitrary_call_count_limit():
    tool_calls = [
        {"capability": "service.status", "arguments": {"service": f"service-{index}"}, "purpose": "检查服务状态"}
        for index in range(75)
    ]
    decision = AgentDecision.model_validate({
        "decision": "invoke_tools",
        "request": request(goal="investigate", scope="runtime", time_focus="current", requested_effect="read"),
        "tool_calls": tool_calls,
    })
    assert len(decision.tool_calls) == 75


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


def test_general_chat_response_has_a_small_explicit_knowledge_reference_contract():
    response = GeneralChatResponse.model_validate({
        "answer": "请先通过可信渠道核对目标服务器的主机指纹。",
        "used_system_knowledge_ids": ["ssh_host_key_mismatch"],
    })
    assert response.used_system_knowledge_ids == ["ssh_host_key_mismatch"]


def test_request_plan_preserves_multiple_goals_constraints_and_dependencies():
    plan = RequestUnderstanding.model_validate({
        "goals": [
            {"id": "g1", "kind": "knowledge", "description": "查询历史认证失败", "time_focus": "historical"},
            {"id": "g2", "kind": "runtime_read", "description": "诊断 backend 当前状态", "time_focus": "current"},
            {"id": "g3", "kind": "change", "description": "必要时启动 backend", "time_focus": "current",
             "depends_on": ["g2"], "condition": "g2 确认 backend 已停止"},
        ],
        "constraints": ["不要修改 MySQL"],
        "summary": "查询历史并按条件恢复 backend",
    })
    assert [goal.kind for goal in plan.goals] == ["knowledge", "runtime_read", "change"]
    assert plan.goals[2].depends_on == ["g2"]
    assert plan.constraints == ["不要修改 MySQL"]
    assert plan.requested_effect == "change"


def test_request_plan_rejects_unknown_dependency():
    with pytest.raises(ValidationError, match="dependencies"):
        RequestUnderstanding.model_validate({
            "goals": [{"id": "g1", "kind": "change", "description": "启动 backend",
                       "time_focus": "current", "depends_on": ["missing"]}],
            "summary": "启动 backend",
        })


def test_request_plan_rejects_dependency_cycles_and_general_tool_calls():
    with pytest.raises(ValidationError):
        RequestUnderstanding.model_validate({
            "goals": [
                {"id": "g1", "kind": "runtime_read", "description": "检查 A",
                 "time_focus": "current", "depends_on": ["g2"]},
                {"id": "g2", "kind": "change", "description": "修复 A",
                 "time_focus": "current", "depends_on": ["g1"]},
            ],
            "summary": "循环依赖",
        })
    with pytest.raises(ValidationError):
        RequestUnderstanding.model_validate({
            "goals": [{"id": "g1", "kind": "general", "description": "打招呼",
                       "time_focus": "timeless"}],
            "recommended_calls": [{"goal_id": "g1", "capability": "service.status",
                                   "arguments": {"service": "backend"}}],
            "summary": "普通对话",
        })


def test_mixed_change_plan_can_collect_read_evidence_before_change():
    plan = {
        "goals": [
            {"id": "g1", "kind": "runtime_read", "description": "检查 backend", "time_focus": "current"},
            {"id": "g2", "kind": "change", "description": "停止时启动 backend", "time_focus": "current",
             "depends_on": ["g1"], "condition": "backend 已停止"},
        ],
        "summary": "检查并按条件启动 backend",
    }
    decision = AgentDecision.model_validate({
        "decision": "invoke_tools", "request": plan,
        "tool_calls": [{"capability": "service.status", "arguments": {"service": "backend"}}],
    })
    assert decision.decision == "invoke_tools"


@pytest.mark.parametrize(("question", "kind"), [
    ("重启 backend 会有什么影响？", "general"),
    ("帮我重启 backend", "change"),
    ("不要重启，只看 backend 当前状态", "runtime_read"),
    ("之前 backend 是否出现过认证失败？", "knowledge"),
])
def test_fallback_router_is_conservative(question, kind):
    plan = _fallback_request_plan(question, {"project_selected": True})
    assert plan.goals[0].kind == kind
