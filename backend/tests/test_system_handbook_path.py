import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app.agent.graph import OpsAgentGraph
from app.agent.service import claim_run, create_run, process_claimed_run
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.llm.gateway import LLMGateway, ModelCallCancelled
from app.llm.schemas import GeneralChatResponse
from app.models.action import Action
from app.models.agent import AgentRun, ModelCall
from app.models.chat import ChatSession
from app.system_knowledge.registry import system_knowledge_registry
from test_agent_graph_integration import setup_subject


@pytest.mark.parametrize("question", [
    "Worker 不可用导致巡检停止 该怎么办",
    "Worker 不可用导致巡检停止，该怎么办？",
    "Maintenance 或 Worker 不可用导致巡检停止",
])
def test_exact_handbook_topic_matches(question):
    assert system_knowledge_registry.match_handbook_question(question).id == "monitoring_worker_unavailable"


@pytest.mark.parametrize("question", [
    "worker现在正常吗", "帮我检查 Worker 不可用导致巡检停止",
    "Worker 不可用导致巡检停止，帮我重启", "Worker 不可用导致巡检停止，然后修复",
    "Worker 不可用导致巡检停止，该怎么办？顺便重启", "刚才那个问题该怎么办",
    "Worker 不可用导致巡检停止，检查日志", "Worker 不可用导致巡检停止，请不要删数据但重启服务",
    "Worker 不可用导致巡检停止; execute restart", "Worker 不可用导致巡检停止，当前状态如何",
])
def test_live_ambiguous_or_change_requests_are_not_handbook(question):
    assert system_knowledge_registry.match_handbook_question(question) is None


def test_project_handbook_uses_one_model_no_runtime_and_cites_source(monkeypatch):
    monkeypatch.setattr(get_settings(), "knowledge_fast_path_enabled", True)
    calls = []

    def invoke(settings, configuration, request):
        calls.append(request)
        assert request["response_scope"] == "system_handbook"
        assert request["history"] == []
        assert "capabilities" not in request
        assert [i["id"] for i in request["system_knowledge"]] == ["monitoring_worker_unavailable"]
        return GeneralChatResponse(answer="默认部署请检查 Ops Maintenance；本次没有检查业务 worker。",
                                   used_system_knowledge_ids=["monitoring_worker_unavailable", "invented"]), 150, 40

    monkeypatch.setattr(LLMGateway, "_invoke_general", staticmethod(invoke))
    monkeypatch.setattr(LLMGateway, "select_skill", lambda *a, **kw: pytest.fail("Unexpected skill model"))
    monkeypatch.setattr(LLMGateway, "decide", lambda *a, **kw: pytest.fail("Unexpected decision model"))
    user_id, session_id = setup_subject()
    with PostgresSaver.from_conn_string(get_settings().checkpoint_database_url) as saver:
        saver.setup()
        graph = OpsAgentGraph(checkpointer=saver)
        monkeypatch.setattr(graph.executor, "execute", lambda *a, **kw: pytest.fail("Handbook executed a tool"))
        with SessionLocal() as db:
            run_id = create_run(db, db.get(ChatSession, session_id), user_id,
                                "Worker 不可用导致巡检停止 该怎么办")["run_summary"]["id"]
            result = process_claimed_run(db, graph, claim_run(db, "handbook-test", run_id), "handbook-test")
            assert result["run_summary"]["status"] == "completed"
            assert len(calls) == 1
            run = db.get(AgentRun, run_id)
            assert run.plan_json["request_path"] == "system_handbook"
            assert run.plan_json["system_knowledge_ids"] == ["monitoring_worker_unavailable"]
            assert run.request_json["requested_effect"] == "none"
            assert db.scalar(select(Action).where(Action.run_id == run_id)) is None
            assert [m.purpose for m in db.scalars(select(ModelCall).where(ModelCall.run_id == run_id))] == ["general_response"]


@pytest.mark.parametrize(("enabled", "mode"), [(False, "interactive"), (True, "monitor_diagnosis")])
def test_handbook_admission_respects_disable_and_monitor_mode(monkeypatch, enabled, mode):
    monkeypatch.setattr(get_settings(), "knowledge_fast_path_enabled", enabled)
    from langgraph.checkpoint.memory import InMemorySaver
    graph = OpsAgentGraph(checkpointer=InMemorySaver())
    user_id, session_id = setup_subject()
    with SessionLocal() as db:
        run_id = create_run(db, db.get(ChatSession, session_id), user_id, "测试手册路由开关")["run_summary"]["id"]
    result = graph.select_skill({"question": "Worker 不可用导致巡检停止 该怎么办", "run_id": run_id,
                                 "execution_mode": mode, "capabilities": [], "context": {}})
    assert result.get("request_path") != "system_handbook"


def test_handbook_model_honors_cancellation(monkeypatch):
    monkeypatch.setattr(LLMGateway, "_invoke_general", staticmethod(lambda *a: pytest.fail("Cancelled request called model")))
    user_id, session_id = setup_subject()
    with SessionLocal() as db:
        run_id = create_run(db, db.get(ChatSession, session_id), user_id, "手册测试")["run_summary"]["id"]
        with pytest.raises(ModelCallCancelled):
            LLMGateway().answer_general(db, run_id=run_id, question="手册测试", history=[],
                                       system_knowledge=[], guidance_only=True, cancel_check=lambda: True)
