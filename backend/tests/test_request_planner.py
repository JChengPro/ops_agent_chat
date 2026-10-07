from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from app.agent.graph import OpsAgentGraph
from app.agent.service import claim_run, create_run, process_claimed_run
from app.api.approvals import ApprovalDecision, decide as decide_approval
from app.capabilities.registry import registry
from app.core.database import SessionLocal
from app.llm.gateway import LLMGateway
from app.llm.schemas import AgentDecision, RequestUnderstanding
from app.models.action import Action, Approval
from app.models.agent import AgentRun
from app.models.chat import ChatSession
from app.models.user import User
from test_agent_graph_integration import FakeExecutor, setup_subject


class PlannerProvider:
    def __init__(self, plan: dict, decision: dict | None = None, final: dict | None = None):
        self.plan = RequestUnderstanding.model_validate(plan)
        self.decision_value = AgentDecision.model_validate(decision) if decision else None
        self.final_value = final
        self.decision_calls = 0
        self.final_calls = 0

    def plan_request(self, **kwargs):
        del kwargs
        return self.plan

    def decide(self, **kwargs):
        del kwargs
        self.decision_calls += 1
        if not self.decision_value:
            raise AssertionError("decision was not expected")
        return self.decision_value

    def answer_final(self, **kwargs):
        del kwargs
        self.final_calls += 1
        if not self.final_value:
            raise AssertionError("final answer was not expected")
        return self.final_value


def _run_and_state(question: str):
    user_id, session_id = setup_subject()
    with SessionLocal() as db:
        run_id = create_run(db, db.get(ChatSession, session_id), user_id, question)["run_summary"]["id"]
    capabilities = [item.model_schema() for item in registry.resolve(
        "docker_compose", {"project.read", "runtime.read", "runtime.change"}
    )]
    state = {
        "run_id": run_id,
        "user_id": user_id,
        "question": question,
        "history": [],
        "execution_mode": "interactive",
        "context": {"project_selected": True, "runtime_type": "docker_compose"},
        "capabilities": capabilities,
        "step_count": 1,
    }
    return state


def test_multi_goal_plan_combines_knowledge_diagnosis_and_change_skills():
    plan = {
        "goals": [
            {"id": "g1", "kind": "knowledge", "description": "查询历史认证失败", "time_focus": "historical"},
            {"id": "g2", "kind": "runtime_read", "description": "诊断 backend 当前状态", "time_focus": "current"},
            {"id": "g3", "kind": "change", "description": "停止时启动 backend", "time_focus": "current",
             "depends_on": ["g2"], "condition": "backend 已停止"},
        ],
        "constraints": ["不要修改 MySQL"],
        "recommended_calls": [
            {"goal_id": "g1", "capability": "experience.search", "arguments": {"query": "MySQL Access denied"}},
            {"goal_id": "g2", "capability": "service.status", "arguments": {"service": "backend"}},
            {"goal_id": "g3", "capability": "service.start", "arguments": {"service": "backend"}},
        ],
        "summary": "查询历史并按条件恢复 backend",
    }
    state = _run_and_state("查询历史故障，检查 backend，停止时启动，但不要修改 MySQL")
    graph = OpsAgentGraph(checkpointer=InMemorySaver(), gateway=LLMGateway(PlannerProvider(plan)))
    result = graph.plan_request(state)

    assert result["request_path"] == "agent"
    assert [goal["kind"] for goal in result["request_plan"]["goals"]] == ["knowledge", "runtime_read", "change"]
    assert {item["name"] for item in result["selected_skills"]} == {"runtime-diagnosis", "controlled-service-change"}
    names = {item["name"] for item in result["capabilities"]}
    assert {"experience.search", "service.status", "service.logs", "service.start"} <= names


def test_runtime_read_plan_does_not_expose_change_capabilities():
    plan = {
        "goals": [{"id": "g1", "kind": "runtime_read", "description": "检查 backend 状态", "time_focus": "current"}],
        "recommended_calls": [
            {"goal_id": "g1", "capability": "service.status", "arguments": {"service": "backend"}},
        ],
        "summary": "只读检查 backend",
    }
    state = _run_and_state("只检查 backend，不执行变更")
    graph = OpsAgentGraph(checkpointer=InMemorySaver(), gateway=LLMGateway(PlannerProvider(plan)))
    result = graph.plan_request(state)

    assert result["request_path"] == "agent"
    assert {item["name"] for item in result["selected_skills"]} == {"runtime-diagnosis"}
    assert all(item["effect"] == "read" for item in result["capabilities"])


def test_conditional_change_is_deferred_until_live_evidence_exists():
    plan = {
        "goals": [
            {"id": "g1", "kind": "runtime_read", "description": "检查 backend", "time_focus": "current"},
            {"id": "g2", "kind": "change", "description": "停止时启动 backend", "time_focus": "current",
             "depends_on": ["g1"], "condition": "backend 已停止"},
        ],
        "recommended_calls": [
            {"goal_id": "g1", "capability": "service.status", "arguments": {"service": "backend"},
             "purpose": "检查状态"},
            {"goal_id": "g2", "capability": "service.start", "arguments": {"service": "backend"},
             "purpose": "停止时启动"},
        ],
        "summary": "检查并按条件启动 backend",
    }
    state = _run_and_state("检查 backend，如果停止就启动")
    gateway = LLMGateway(PlannerProvider(plan))
    graph = OpsAgentGraph(checkpointer=InMemorySaver(), gateway=gateway)
    planned = graph.plan_request(state)
    result = graph.decide({**state, **planned, "evidence": []})

    assert result["decision"]["decision"] == "invoke_tools"
    assert [item["capability"] for item in result["pending_calls"]] == ["service.status"]
    assert [item["capability"] for item in result["deferred_calls"]] == ["service.start"]


def test_simple_runtime_request_uses_planner_then_final_answer_without_decision_round():
    plan = {
        "goals": [{"id": "g1", "kind": "runtime_read", "description": "检查 redis 状态",
                   "time_focus": "current"}],
        "recommended_calls": [{"goal_id": "g1", "capability": "service.status",
                               "arguments": {"service": "redis"}, "purpose": "读取实时状态"}],
        "summary": "检查 redis 当前状态",
    }
    provider = PlannerProvider(plan, final={
        "answer": "Redis 当前正在运行。",
        "claims": [{"text": "Redis 当前正在运行。", "claim_type": "fact",
                    "evidence_ids": [], "confidence": 0.9}],
    })
    user_id, session_id = setup_subject()
    graph = OpsAgentGraph(checkpointer=InMemorySaver(), gateway=LLMGateway(provider), executor=FakeExecutor())
    with SessionLocal() as db:
        run_id = create_run(db, db.get(ChatSession, session_id), user_id, "Redis 现在运行吗？")["run_summary"]["id"]
        result = process_claimed_run(db, graph, claim_run(db, "planner-test", run_id), "planner-test")

    assert result["run_summary"]["status"] == "completed"
    assert result["assistant_message"]["content"] == "Redis 当前正在运行。"
    assert provider.decision_calls == 0
    assert provider.final_calls == 1


def test_direct_change_uses_planner_approval_and_final_answer_without_extra_decision_round():
    plan = {
        "goals": [{"id": "g1", "kind": "change", "description": "重启 redis",
                   "time_focus": "current"}],
        "recommended_calls": [{"goal_id": "g1", "capability": "service.restart",
                               "arguments": {"service": "redis"}, "purpose": "重启服务"}],
        "summary": "重启 redis",
    }
    provider = PlannerProvider(plan, final={
        "answer": "Redis 已重启并通过状态验证。",
        "claims": [{"text": "Redis 已重启并通过状态验证。", "claim_type": "fact",
                    "evidence_ids": [], "confidence": 0.9}],
    })
    user_id, session_id = setup_subject()
    graph = OpsAgentGraph(checkpointer=InMemorySaver(), gateway=LLMGateway(provider), executor=FakeExecutor())
    with SessionLocal() as db:
        run_id = create_run(
            db, db.get(ChatSession, session_id), user_id, "重启 Redis"
        )["run_summary"]["id"]
        waiting = process_claimed_run(
            db, graph, claim_run(db, "planner-change", run_id), "planner-change"
        )
        assert waiting["run_summary"]["status"] == "waiting_for_approval"
        approval = db.scalar(select(Approval).join(Action).where(Action.run_id == run_id))
        assert approval and approval.decision == "pending"

        decide_approval(
            approval.id,
            ApprovalDecision(action_hash=approval.action_hash),
            "approved",
            db,
            db.get(User, user_id),
        )
        run = db.get(AgentRun, run_id)
        finished = process_claimed_run(
            db, graph, claim_run(db, "planner-change", run.id), "planner-change"
        )

        assert finished["run_summary"]["status"] == "completed"
        assert finished["assistant_message"]["content"] == "Redis 已重启并通过状态验证。"
        change_action = db.scalar(
            select(Action).where(Action.run_id == run_id, Action.capability_name == "service.restart")
        )
        assert change_action.status == "verified"
        assert provider.decision_calls == 0
        assert provider.final_calls == 1
