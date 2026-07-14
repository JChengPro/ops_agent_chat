from datetime import datetime, timezone

from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app.agent.graph import OpsAgentGraph
from app.agent.service import resume_run, start_run
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.llm.gateway import LLMGateway
from app.llm.providers.fake import FakeDecisionProvider
from app.models.action import Action, Approval
from app.models.agent import AgentRun
from app.models.chat import ChatSession
from app.models.project import Environment, Project
from app.models.context import ContextSource
from app.context.service import query_relationships, upsert_entity, upsert_relationship
from app.models.user import User
from app.services.seed_service import seed_initial_data
from app.evidence.service import record_result
from app.runtime.adapters.base import AdapterResult
from app.models.evidence import EvidenceClaim, EvidenceClaimLink


def req(goal="answer", scope="general", effect="none", time="timeless"):
    return {"goal": goal, "scope": scope, "time_focus": time, "requested_effect": effect, "subjects": [], "desired_output": "answer", "constraints": [], "confidence": 0.95, "summary": goal}


class FakeExecutor:
    def execute(self, db, action, capability):
        data = {"stdout": '{"State":"running"}\n'} if capability.name == "service.status" else {"state": "running"}
        evidence = record_result(db, action, "fake", AdapterResult("success", f"Observed {capability.name}", data))
        db.flush()
        return {"evidence_id": evidence.id, "capability": capability.name, "status": "success", "summary": evidence.summary, "data": evidence.data_json, "observed_at": evidence.observed_at.isoformat(), "fresh_until": evidence.fresh_until.isoformat() if evidence.fresh_until else None}


def setup_subject():
    with SessionLocal() as db:
        seed_initial_data(db)
        user = db.scalar(select(User).limit(1)); project = db.scalar(select(Project).where(Project.owner_id == user.id)); environment = db.scalar(select(Environment).where(Environment.project_id == project.id))
        session = ChatSession(project_id=project.id, environment_id=environment.id, user_id=user.id, title="test")
        db.add(session); db.commit(); db.refresh(session)
        return user.id, session.id


def test_direct_answer_and_read_investigation_and_approval_resume():
    user_id, session_id = setup_subject()
    decisions = [
        {"decision":"respond","request":req(),"tool_calls":[],"answer":"通用回答","clarification_question":None},
        {"decision":"invoke_tools","request":req("investigate","runtime","read","current"),"tool_calls":[{"capability":"service.status","arguments":{"service":"redis"},"purpose":"read status"}],"answer":None,"clarification_question":None},
        {"decision":"respond","request":req("investigate","runtime","read","current"),"tool_calls":[],"answer":"Redis 当前正在运行。","clarification_question":None},
        {"decision":"propose_change","request":req("change","runtime","change","current"),"tool_calls":[{"capability":"service.restart","arguments":{"service":"redis"},"purpose":"restart"}],"answer":None,"clarification_question":None},
        {"decision":"respond","request":req("change","runtime","change","current"),"tool_calls":[],"answer":"Redis 已重启并完成验证。","clarification_question":None},
    ]
    with PostgresSaver.from_conn_string(get_settings().checkpoint_database_url) as saver:
        saver.setup()
        graph = OpsAgentGraph(checkpointer=saver, gateway=LLMGateway(FakeDecisionProvider(decisions)), executor=FakeExecutor())
        with SessionLocal() as db:
            session = db.get(ChatSession, session_id)
            direct = start_run(db, graph, session, user_id, "删除容器通常有什么后果？")
            assert direct["run_summary"]["status"] == "completed"
            read = start_run(db, graph, session, user_id, "Redis 现在怎么样？")
            assert read["assistant_message"]["content"] == "Redis 当前正在运行。"
            assert read["assistant_message"]["metadata_json"]["evidence_ids"]
            claim = db.scalar(select(EvidenceClaim).where(EvidenceClaim.message_id == read["assistant_message"]["id"]))
            assert db.scalar(select(EvidenceClaimLink.id).where(EvidenceClaimLink.claim_id == claim.id)) is not None
            change = start_run(db, graph, session, user_id, "重启 Redis")
            assert change["run_summary"]["status"] == "waiting_for_approval"
            approval = db.scalar(select(Approval).join(Action).where(Action.run_id == change["run_summary"]["id"]))
            assert approval and approval.decision == "pending"
            prepared = list(db.scalars(select(Action).where(Action.run_id == change["run_summary"]["id"])))
            assert {(item.capability_name, item.status) for item in prepared} == {("service.restart", "waiting_for_approval"), ("service.status", "succeeded")}
            approval.decision = "approved"; approval.decided_at = datetime.now(timezone.utc); db.get(Action, approval.action_id).status = "approved"; db.commit()
            run = db.get(AgentRun, change["run_summary"]["id"])
            finished = resume_run(db, graph, run)
            assert finished["assistant_message"]["content"] == "Redis 已重启并完成验证。"
            status_actions = list(db.scalars(select(Action).where(Action.run_id == run.id, Action.capability_name == "service.status")))
            assert len(status_actions) == 3  # initial precheck, post-approval recheck, verifier


def test_project_relationship_recursive_query_and_source_conflict():
    user_id, session_id = setup_subject()
    del user_id
    with SessionLocal() as db:
        session = db.get(ChatSession, session_id); env = db.get(Environment, session.environment_id)
        source_a = ContextSource(project_id=env.project_id, environment_id=env.id, source_type="manual", source_ref="test-a", collector_name="test")
        source_b = ContextSource(project_id=env.project_id, environment_id=env.id, source_type="project_file", source_ref="test-b", collector_name="test")
        db.add_all([source_a, source_b]); db.flush()
        gateway = upsert_entity(db, project_id=env.project_id, environment_id=env.id, source_id=source_a.id, entity_type="service", canonical_name="test-gateway", properties={"port": 80})
        api = upsert_entity(db, project_id=env.project_id, environment_id=env.id, source_id=source_a.id, entity_type="service", canonical_name="test-api", properties={"port": 8080})
        database = upsert_entity(db, project_id=env.project_id, environment_id=env.id, source_id=source_a.id, entity_type="service", canonical_name="test-db", properties={"port": 5432})
        upsert_relationship(db, project_id=env.project_id, environment_id=env.id, source_id=source_a.id, from_entity_id=gateway.id, to_entity_id=api.id, relation_type="DEPENDS_ON")
        upsert_relationship(db, project_id=env.project_id, environment_id=env.id, source_id=source_a.id, from_entity_id=api.id, to_entity_id=database.id, relation_type="DEPENDS_ON")
        conflicted = upsert_entity(db, project_id=env.project_id, environment_id=env.id, source_id=source_b.id, entity_type="service", canonical_name="test-api", properties={"port": 9090})
        db.commit()
        assert conflicted.confidence < 1
        assert conflicted.properties_json["_has_source_conflict"] is True
        result = query_relationships(db, env.project_id, env.id, "test-gateway", 3, reverse=False)
        assert any(path["path"] == ["test-gateway", "test-api", "test-db"] for path in result["paths"])
