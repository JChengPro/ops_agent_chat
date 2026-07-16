from datetime import datetime, timezone

from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app.agent.graph import OpsAgentGraph
from app.agent.service import _persist_claims, claim_run, create_run, process_claimed_run
from app.api.approvals import ApprovalDecision, decide as decide_approval
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
    def __init__(self):
        self.calls = 0

    def execute(self, db, action, capability):
        self.calls += 1
        data = {"stdout": '{"State":"running"}\n'} if capability.name == "service.status" else {"state": "running"}
        evidence = record_result(db, action, "fake", AdapterResult("success", f"Observed {capability.name}", data))
        db.flush()
        return {"evidence_id": evidence.id, "capability": capability.name, "status": "success", "summary": evidence.summary, "data": evidence.data_json, "observed_at": evidence.observed_at.isoformat(), "fresh_until": evidence.fresh_until.isoformat() if evidence.fresh_until else None}

    def rollback(self, db, action, capability):
        del db, action, capability
        return {"status": "success", "summary": "rolled back"}

    def finalize(self, db, action, capability):
        del db, action, capability


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
        executor = FakeExecutor()
        graph = OpsAgentGraph(checkpointer=saver, gateway=LLMGateway(FakeDecisionProvider(decisions)), executor=executor)
        with SessionLocal() as db:
            session = db.get(ChatSession, session_id)
            queued = create_run(db, session, user_id, "删除容器通常有什么后果？")
            direct = process_claimed_run(db, graph, claim_run(db, "test-worker", queued["run_summary"]["id"]), "test-worker")
            assert direct["run_summary"]["status"] == "completed"
            direct_claim = db.scalar(select(EvidenceClaim).where(EvidenceClaim.message_id == direct["assistant_message"]["id"]))
            assert direct_claim.claim_type == "general_knowledge"
            assert direct_claim.confidence <= 0.7
            queued = create_run(db, session, user_id, "Redis 现在怎么样？")
            read = process_claimed_run(db, graph, claim_run(db, "test-worker", queued["run_summary"]["id"]), "test-worker")
            assert read["assistant_message"]["content"] == "Redis 当前正在运行。"
            assert read["assistant_message"]["metadata_json"]["evidence_ids"]
            claim = db.scalar(select(EvidenceClaim).where(EvidenceClaim.message_id == read["assistant_message"]["id"]))
            assert claim.claim_type == "inference"
            assert db.scalar(select(EvidenceClaimLink.id).where(EvidenceClaimLink.claim_id == claim.id)) is None
            evidence_id = read["assistant_message"]["metadata_json"]["evidence_ids"][0]
            _persist_claims(db, read["assistant_message"]["id"], read["assistant_message"]["content"], [
                {"text": "Redis 当前正在运行。", "claim_type": "fact", "evidence_ids": [evidence_id], "confidence": 0.9},
                {"text": "建议继续观察。", "claim_type": "recommendation", "evidence_ids": [], "confidence": 0.7},
            ], [evidence_id])
            db.commit()
            claims = list(db.scalars(select(EvidenceClaim).where(EvidenceClaim.message_id == read["assistant_message"]["id"])))
            assert len(claims) == 2
            assert len(list(db.scalars(select(EvidenceClaimLink.id).where(EvidenceClaimLink.claim_id.in_([item.id for item in claims]))))) == 1
            queued = create_run(db, session, user_id, "重启 Redis")
            change = process_claimed_run(db, graph, claim_run(db, "test-worker", queued["run_summary"]["id"]), "test-worker")
            assert change["run_summary"]["status"] == "waiting_for_approval"
            approval = db.scalar(select(Approval).join(Action).where(Action.run_id == change["run_summary"]["id"]))
            assert approval and approval.decision == "pending"
            prepared = list(db.scalars(select(Action).where(Action.run_id == change["run_summary"]["id"])))
            assert {(item.capability_name, item.status) for item in prepared} == {("service.restart", "waiting_for_approval"), ("service.status", "succeeded")}
            owner = db.get(User, user_id)
            decide_approval(approval.id, ApprovalDecision(action_hash=approval.action_hash), "approved", db, owner)
            run = db.get(AgentRun, change["run_summary"]["id"])
            assert run.status == "queued" and run.current_step == "queued_resume"
            finished = process_claimed_run(db, graph, claim_run(db, "test-worker", run.id), "test-worker")
            assert finished["assistant_message"]["content"] == "Redis 已重启并完成验证。"
            status_actions = list(db.scalars(select(Action).where(Action.run_id == run.id, Action.capability_name == "service.status")))
            assert len(status_actions) == 3  # initial precheck, post-approval recheck, verifier
            change_action = db.scalar(select(Action).where(Action.run_id == run.id, Action.capability_name == "service.restart"))
            assert change_action.status == "verified"
            calls_before_retry = executor.calls
            graph.execute({"run_id": run.id, "user_id": user_id, "action_ids": [change_action.id], "evidence": [], "tool_call_count": 0, "step_count": 0})
            assert executor.calls == calls_before_retry


def test_failed_post_change_verification_triggers_automatic_rollback():
    user_id, session_id = setup_subject()
    decisions = [
        {"decision":"propose_change","request":req("change","runtime","change","current"),"tool_calls":[{"capability":"service.restart","arguments":{"service":"redis"},"purpose":"restart"}],"answer":None,"clarification_question":None},
        {"decision":"respond","request":req("change","runtime","change","current"),"tool_calls":[],"answer":"变更后的状态检查未通过，系统已执行恢复步骤。","clarification_question":None},
    ]

    class VerificationFailureExecutor(FakeExecutor):
        def __init__(self):
            super().__init__()
            self.status_calls = 0
            self.rollback_calls = 0

        def execute(self, db, action, capability):
            self.calls += 1
            if capability.name == "service.status":
                self.status_calls += 1
                result = AdapterResult(
                    "failed" if self.status_calls == 3 else "success",
                    "Post-change status failed" if self.status_calls == 3 else "Service is running",
                    {"stdout": "" if self.status_calls == 3 else '{"State":"running"}\n'},
                )
            else:
                result = AdapterResult("success", "Restart command accepted", {"state": "changed"})
            evidence = record_result(db, action, "fake", result)
            db.flush()
            return {"evidence_id": evidence.id, "capability": capability.name, "status": result.status, "summary": result.summary, "data": evidence.data_json, "observed_at": evidence.observed_at.isoformat(), "fresh_until": evidence.fresh_until.isoformat() if evidence.fresh_until else None}

        def rollback(self, db, action, capability):
            del db, action, capability
            self.rollback_calls += 1
            return {"status": "success", "summary": "Original service state restored"}

    executor = VerificationFailureExecutor()
    with PostgresSaver.from_conn_string(get_settings().checkpoint_database_url) as saver:
        saver.setup()
        graph = OpsAgentGraph(checkpointer=saver, gateway=LLMGateway(FakeDecisionProvider(decisions)), executor=executor)
        with SessionLocal() as db:
            session = db.get(ChatSession, session_id)
            queued = create_run(db, session, user_id, "重启 Redis")
            waiting = process_claimed_run(db, graph, claim_run(db, "rollback-worker", queued["run_summary"]["id"]), "rollback-worker")
            approval = db.scalar(select(Approval).join(Action).where(Action.run_id == waiting["run_summary"]["id"]))
            decide_approval(approval.id, ApprovalDecision(action_hash=approval.action_hash), "approved", db, db.get(User, user_id))
            finished = process_claimed_run(db, graph, claim_run(db, "rollback-worker", waiting["run_summary"]["id"]), "rollback-worker")
            action = db.scalar(select(Action).where(Action.run_id == waiting["run_summary"]["id"], Action.capability_name == "service.restart"))
            assert finished["run_summary"]["status"] == "completed"
            assert action.status == "rolled_back"
            assert executor.rollback_calls == 1


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
