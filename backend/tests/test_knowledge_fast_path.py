import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app.agent.graph import OpsAgentGraph
from app.agent.routing import compact_evidence, knowledge_route
from app.agent.service import claim_run, create_run, process_claimed_run
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.llm.gateway import LLMGateway, ModelCallCancelled
from app.llm.schemas import KnowledgeResponse
from app.models.action import Action, PolicyDecision
from app.models.agent import AgentRun, ModelCall
from app.models.chat import ChatSession
from app.models.evidence import RuntimeEvidence
from test_agent_graph_integration import setup_subject


CAPS = [{"name": "experience.search"}, {"name": "service.restart"}]


@pytest.mark.parametrize("question", [
    "之前有没有出现过 MySQL Access denied？", "项目文档里 backend 怎么配置？",
    "Search verified historical experience for backend startup failures.",
    "查询历史经验。不要执行命令。", "Summarize historical knowledge; do not execute changes.",
    "之前有没有出现过 MySQL Access denied？请只查询历史经验，不要执行任何变更。",
    "查询历史经验,请勿执行命令。",
])
def test_explicit_knowledge_admitted(question):
    assert knowledge_route(question, CAPS)


@pytest.mark.parametrize("question", [
    "backend 现在挂了吗", "查历史经验，然后重启 backend", "看看文档，顺便修复服务",
    "按照刚才那个经验处理", "历史文档说健康，当前服务正常吗", "之前是怎么重启的，帮我执行",
    "Search history and restart backend", "Check current production status",
    "Restart backend; do not delete anything", "你好",
    "查历史经验。不要删除，但是重启 backend", "Read history; do not delete but restart backend",
    "项目文档里有说明，检查 backend 日志", "查询历史经验，然后清空缓存",
    "查询历史经验，不要删除，但要重启 backend。",
])
def test_ambiguous_live_or_change_requests_use_original_loop(question):
    assert not knowledge_route(question, CAPS)
    assert not knowledge_route("查询历史经验", [], execution_mode="interactive")
    assert not knowledge_route("查询历史经验", CAPS, execution_mode="monitor_diagnosis")


def test_compaction_deduplicates_and_keeps_source_ids():
    observation = {"capability": "experience.search", "evidence_id": "e1", "data": {"items": [
        {"item_id": 1, "chunk_key": "a", "content": "abcdefgh", "title": "source"},
        {"item_id": 2, "chunk_key": "b", "content": "12345678", "title": "source2"},
    ]}}
    sources = compact_evidence([observation, observation], 10)
    assert len(sources) == 2
    assert sum(len(item["content"]) for item in sources) == 10
    assert sources[0]["evidence_id"] == "e1" and sources[1]["item_id"] == 2


def test_fast_path_uses_one_answer_call_and_keeps_governed_execution(monkeypatch):
    from app.experience import service as experience
    monkeypatch.setattr(get_settings(), "knowledge_fast_path_enabled", True)
    monkeypatch.setattr(experience, "configured_embedding_provider", lambda: None)
    calls = []

    def invoke(settings, configuration, request):
        calls.append(request)
        assert "capabilities" not in request
        return KnowledgeResponse(answer="No matching historical incident is established.", claims=[]), 100, 30

    monkeypatch.setattr(LLMGateway, "_invoke_knowledge", staticmethod(invoke))
    monkeypatch.setattr(LLMGateway, "select_skill", lambda *a, **kw: pytest.fail("Unexpected skill model"))
    monkeypatch.setattr(LLMGateway, "decide", lambda *a, **kw: pytest.fail("Unexpected planning model"))
    user_id, session_id = setup_subject()
    with PostgresSaver.from_conn_string(get_settings().checkpoint_database_url) as saver:
        saver.setup()
        graph = OpsAgentGraph(checkpointer=saver)
        with SessionLocal() as db:
            queued = create_run(db, db.get(ChatSession, session_id), user_id, "之前有没有出现过 MySQL Access denied？")
            run_id = queued["run_summary"]["id"]
            result = process_claimed_run(db, graph, claim_run(db, "test", run_id), "test")
            assert result["run_summary"]["status"] == "completed"
            assert len(calls) == 1
            action = db.scalar(select(Action).where(Action.run_id == run_id))
            assert action.capability_name == "experience.search" and action.effect == "read"
            assert action.action_hash and action.execution_token
            assert db.scalar(select(PolicyDecision).where(PolicyDecision.action_id == action.id)).decision == "allow"
            assert db.scalar(select(RuntimeEvidence).where(RuntimeEvidence.run_id == run_id))
            model_calls = db.scalars(select(ModelCall).where(ModelCall.run_id == run_id)).all()
            assert [item.purpose for item in model_calls] == ["knowledge_response"]
            assert db.get(AgentRun, run_id).plan_json["request_path"] == "knowledge"


def test_knowledge_filters_fabricated_citations_and_honors_cancellation(monkeypatch):
    user_id, session_id = setup_subject()
    response = KnowledgeResponse(answer="A source was retrieved.", claims=[{
        "text": "Documentation guidance", "claim_type": "general_knowledge",
        "evidence_ids": ["known", "invented"], "experience_item_ids": [1, 999],
        "context_source_ids": [999],
    }])
    monkeypatch.setattr(LLMGateway, "_invoke_knowledge", staticmethod(lambda *a: (response, 100, 40)))
    with SessionLocal() as db:
        run_id = create_run(db, db.get(ChatSession, session_id), user_id, "查询历史经验")["run_summary"]["id"]
        result = LLMGateway().answer_knowledge(db, run_id=run_id, question="question", sources=[
            {"evidence_id": "known", "item_id": 1},
        ])
        claim = result.claims[0]
        assert claim.evidence_ids == ["known"] and claim.experience_item_ids == [1]
        assert claim.context_source_ids == []
        monkeypatch.setattr(LLMGateway, "_invoke_knowledge", staticmethod(lambda *a: pytest.fail("Cancelled request invoked model")))
        with pytest.raises(ModelCallCancelled):
            LLMGateway().answer_knowledge(db, run_id=run_id, question="question", sources=[], cancel_check=lambda: True)
        calls = db.scalars(select(ModelCall).where(ModelCall.run_id == run_id)).all()
        assert sorted(call.status for call in calls) == ["cancelled", "success"]
