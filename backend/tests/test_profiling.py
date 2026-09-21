from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.errors import GraphInterrupt
from sqlalchemy import select

from app import profiling
from app.agent.graph import OpsAgentGraph
from app.agent.service import claim_run, create_run, process_claimed_run
from app.api.agent_runs import profile
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.experience import service as experience
from app.llm.gateway import LLMGateway
from app.llm.providers.fake import FakeDecisionProvider
from app.models.agent import AgentRun
from app.models.chat import ChatSession
from app.models.project import Environment, Project
from app.models.profiling import ProfileSpan
from app.models.user import User
from app.services.seed_service import seed_initial_data


@pytest.fixture
def recorder():
    recorder = profiling.Recorder(str(uuid4()))
    token = profiling._current.set(recorder)
    yield recorder
    profiling._current.reset(token)


def test_nested_stages_thread_context_usage_and_exception_identity(recorder):
    completion = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=123, completion_tokens=45))
    with profiling.measure("llm.decision"):
        with ThreadPoolExecutor(max_workers=1) as pool:
            actual = pool.submit(copy_context().run, profiling.request_call,
                                 lambda **kwargs: completion, purpose="decision", model="test-model").result()
    assert actual is completion
    request = next(item for item in recorder.rows if item["stage"] == "llm.request")
    assert request["metadata_json"] == {"purpose": "decision", "model": "test-model", "input_tokens": 123, "output_tokens": 45}
    assert request["started_at"] <= request["ended_at"]
    assert request["latency_ms"] >= 0
    error = ValueError("original failure")
    with pytest.raises(ValueError) as caught:
        with profiling.measure("failure"):
            raise error
    assert caught.value is error
    with pytest.raises(GraphInterrupt):
        with profiling.measure("approval"):
            raise GraphInterrupt(())
    assert recorder.rows[-1]["status"] == "interrupted"


def test_storage_failure_cannot_change_success_or_exception(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("database offline")
    monkeypatch.setattr(profiling.psycopg, "connect", unavailable)
    db = SimpleNamespace(info={})
    run = SimpleNamespace(id=str(uuid4()))

    @profiling.profile_worker
    def success(db, agent, run, worker_id):
        with profiling.measure("work"):
            return {"answer": "unchanged"}

    assert success(db, None, run, "test") == {"answer": "unchanged"}
    error = RuntimeError("business failure")

    @profiling.profile_worker
    def failure(db, agent, run, worker_id):
        raise error

    with pytest.raises(RuntimeError) as caught:
        failure(db, None, run, "test")
    assert caught.value is error
    assert profiling._current.get() is None


def test_rag_cache_and_failure_preserve_results(monkeypatch, recorder):
    from test_hybrid_retrieval import _RecordingSession, _row, _CountingReranker, _FailingReranker
    from app.reranking.service import RerankCache

    monkeypatch.setattr(experience, "configured_embedding_provider", lambda: None)
    cache = RerankCache(max_entries=10, ttl_seconds=300)
    monkeypatch.setattr(experience, "configured_rerank_cache", lambda: cache)
    reranker = _CountingReranker()
    db = _RecordingSession([_row(1, "first"), _row(2, "second"), _row(3, "third")])
    first = experience.search_experience(db, 1, "backend", limit=2, reranker=reranker)
    second = experience.search_experience(db, 1, "backend", limit=2, reranker=reranker)
    assert first["items"] == second["items"]
    assert second["rerank_cache_hit"] and reranker.calls == 1
    cached = [item for item in recorder.rows if item["stage"] == "rag.rerank"][-1]
    assert cached["metadata_json"]["rerank_cache_hit"] is True
    assert cached["metadata_json"]["candidate_count"] == 3
    failed = experience.search_experience(db, 1, "backend", limit=2, reranker=_FailingReranker())
    assert failed["rerank_error"] == "TimeoutError"
    assert [item["item_id"] for item in failed["items"]] == [1, 2]
    assert [item for item in recorder.rows if item["stage"] == "rag.rerank"][-1]["status"] == "failed"


def test_real_graph_two_rounds_profile_and_authorization(monkeypatch):
    monkeypatch.setattr(experience, "configured_embedding_provider", lambda: None)
    with PostgresSaver.from_conn_string(get_settings().checkpoint_database_url) as saver:
        saver.setup()
    request = {"goal": "investigate", "scope": "project", "time_focus": "historical",
               "requested_effect": "read", "subjects": [], "desired_output": "answer",
               "constraints": [], "confidence": 0.9, "summary": "search experience"}
    decisions = [
        {"decision": "invoke_tools", "request": request, "tool_calls": [
            {"capability": "experience.search", "arguments": {"query": "backend"}, "purpose": "history"}]},
        {"decision": "respond", "request": request, "tool_calls": [], "answer": "No verified experience."},
    ]
    with SessionLocal() as db:
        seed_initial_data(db)
        user = db.scalar(select(User).limit(1))
        project = db.scalar(select(Project).where(Project.owner_id == user.id))
        environment = db.scalar(select(Environment).where(Environment.project_id == project.id))
        session = ChatSession(user_id=user.id, project_id=project.id, environment_id=environment.id, title="profile")
        db.add(session)
        db.commit()
        queued = create_run(db, session, user.id, "Search verified backend experience")
        run_id = queued["run_summary"]["id"]
        with PostgresSaver.from_conn_string(get_settings().checkpoint_database_url) as saver:
            graph = OpsAgentGraph(checkpointer=saver, gateway=LLMGateway(FakeDecisionProvider(decisions)))
            result = process_claimed_run(db, graph, claim_run(db, "profile-test", run_id), "profile-test")
        assert result["run_summary"]["status"] == "completed"
        data = profile(run_id, db, user)
        stages = {item["stage"] for item in data["timeline"]}
        assert {"run.total", "queue.wait", "context.initialize", "capabilities.resolve", "skill.selection",
                "llm.decision", "rag.search", "rag.lexical", "rag.embedding", "rag.vector", "rag.rrf",
                "rag.rerank", "rag.result_assembly", "tool.execute", "answer.persist"} <= stages
        assert [item["round_index"] for item in data["timeline"] if item["stage"] == "llm.decision"] == [1, 2]
        assert data["profile_status"] == "recorded"
        assert data["client_observed_latency_ms"] is None
        assert data["server_latency_ms"] > 0
        assert data["top_stages"][0]["latency_ms"] >= data["top_stages"][-1]["latency_ms"]
        assert data["answer_committed_at"] >= db.get(AgentRun, run_id).completed_at
        with pytest.raises(HTTPException) as caught:
            profile(run_id, db, SimpleNamespace(id=-999))
        assert caught.value.status_code == 404


def test_queue_marker_is_only_published_after_commit():
    run_id = str(uuid4())
    with SessionLocal() as db:
        profiling.queue_after_commit(db, run_id)
        db.execute(select(1))
        db.rollback()
        db.commit()
        assert db.scalar(select(ProfileSpan).where(ProfileSpan.run_id == run_id)) is None


def test_approval_resume_keeps_both_queue_waits_and_global_rounds():
    from test_agent_graph_integration import FakeExecutor, req, setup_subject
    from app.api.approvals import ApprovalDecision, decide
    from app.models.action import Action, Approval

    user_id, session_id = setup_subject()
    decisions = [
        {"decision": "propose_change", "request": req("change", "runtime", "change", "current"),
         "tool_calls": [{"capability": "service.restart", "arguments": {"service": "redis"}}]},
        {"decision": "respond", "request": req(), "answer": "Restart verified.", "tool_calls": []},
    ]
    with PostgresSaver.from_conn_string(get_settings().checkpoint_database_url) as saver:
        saver.setup()
        graph = OpsAgentGraph(checkpointer=saver, gateway=LLMGateway(FakeDecisionProvider(decisions)), executor=FakeExecutor())
        with SessionLocal() as db:
            queued = create_run(db, db.get(ChatSession, session_id), user_id, "Restart redis")
            run_id = queued["run_summary"]["id"]
            first = process_claimed_run(db, graph, claim_run(db, "test", run_id), "test")
            assert first["run_summary"]["status"] == "waiting_for_approval"
            user = db.get(User, user_id)
            assert profile(run_id, db, user)["server_latency_ms"] is None
            approval = db.scalar(select(Approval).join(Action).where(Action.run_id == run_id))
            decide(approval.id, ApprovalDecision(action_hash=approval.action_hash), "approved", db, user)
            result = process_claimed_run(db, graph, claim_run(db, "test", run_id), "test")
            assert result["run_summary"]["status"] == "completed"
            data = profile(run_id, db, user)
            assert len([item for item in data["timeline"] if item["stage"] == "queue.wait"]) == 2
            assert [item["round_index"] for item in data["timeline"] if item["stage"] == "llm.decision"] == [1, 2]
            assert data["profile_status"] == "recorded"


def test_embedding_failure_is_measured_without_changing_lexical_fallback(recorder):
    from test_hybrid_retrieval import _RecordingSession, _row

    class BrokenEmbedder:
        def embed(self, texts):
            raise TimeoutError("embedding unavailable")

    result = experience.search_experience(_RecordingSession([_row(1, "backend")]), 1,
                                         "backend", embedder=BrokenEmbedder())
    assert result["embedding_error"] == "TimeoutError"
    assert result["result_count"] == 1
    stages = {item["stage"]: item for item in recorder.rows}
    assert stages["rag.embedding"]["status"] == "failed"
    assert stages["rag.vector"]["status"] == "skipped"
    assert stages["rag.search"]["status"] == "success"


def test_total_uses_monotonic_worker_interval_when_wall_clock_steps_back():
    start = datetime(2026, 9, 21, tzinfo=timezone.utc)
    run = SimpleNamespace(id=str(uuid4()), status="completed")
    end = start + timedelta(seconds=9)
    rows = [
        ProfileSpan(**profiling.row(run.id, "run.total", start, end, 9000)),
        ProfileSpan(**profiling.row(run.id, "profile.segment", start + timedelta(seconds=1), end, 10000)),
    ]
    db = SimpleNamespace(scalars=lambda statement: SimpleNamespace(all=lambda: rows))
    data = profiling.report(db, run)
    assert data["server_latency_ms"] == 11000
    total = data["timeline"][0]
    assert total["metadata"]["wall_latency_ms"] == 9000
    assert total["metadata"]["worker_clock_correction_ms"] == 2000
    assert rows[0].metadata_json == {}
