from datetime import datetime, timedelta, timezone
import json
from typing import Any
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import select

from app.agent.state import AgentState
from app.audit.service import append_audit_event
from app.capabilities.registry import registry
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.llm.gateway import LLMGateway
from app.models.action import Action, Approval, PolicyDecision
from app.models.agent import AgentRun, AgentStep
from app.models.project import Environment, Project, ProjectMember
from app.policy.action_hash import compute_action_hash
from app.policy.engine import PolicyEngine, permissions_for_role
from app.runtime.executor import RuntimeExecutor


class OpsAgentGraph:
    def __init__(self, *, checkpointer, gateway: LLMGateway | None = None, executor: RuntimeExecutor | None = None) -> None:
        self.gateway = gateway or LLMGateway()
        self.executor = executor or RuntimeExecutor()
        self.policy = PolicyEngine()
        self.graph = self._build().compile(checkpointer=checkpointer)

    def _build(self):
        graph = StateGraph(AgentState)
        graph.add_node("resolve_capabilities", self.resolve_capabilities)
        graph.add_node("decide", self.decide)
        graph.add_node("prepare_actions", self.prepare_actions)
        graph.add_node("await_approval", self.await_approval)
        graph.add_node("execute", self.execute)
        graph.add_node("finish", self.finish)
        graph.add_edge(START, "resolve_capabilities")
        graph.add_edge("resolve_capabilities", "decide")
        graph.add_conditional_edges("decide", self.route_decision, {"prepare": "prepare_actions", "finish": "finish"})
        graph.add_conditional_edges("prepare_actions", self.route_prepared, {"approval": "await_approval", "execute": "execute", "decide": "decide", "finish": "finish"})
        graph.add_conditional_edges("await_approval", self.route_approval, {"execute": "execute", "finish": "finish"})
        graph.add_conditional_edges("execute", self.route_after_execute, {"decide": "decide", "finish": "finish"})
        graph.add_edge("finish", END)
        return graph

    def resolve_capabilities(self, state: AgentState) -> dict:
        with SessionLocal() as db:
            runtime_type = None
            context: dict[str, Any] = {"project_selected": False}
            permissions: set[str] = set()
            if state.get("project_id") and state.get("environment_id"):
                project = db.get(Project, state["project_id"])
                environment = db.get(Environment, state["environment_id"])
                if project and project.is_active and environment and environment.is_active and environment.project_id == project.id:
                    role = "owner" if project.owner_id == state["user_id"] else db.scalar(
                        select(ProjectMember.role).where(ProjectMember.project_id == project.id, ProjectMember.user_id == state["user_id"])
                    )
                    permissions = permissions_for_role(role)
                    runtime_type = environment.runtime_type
                    context = {
                        "project_selected": True,
                        "project_id": project.id,
                        "project_name": project.name,
                        "project_description": project.description,
                        "environment_id": environment.id,
                        "environment_name": environment.name,
                        "runtime_type": environment.runtime_type,
                        "policy_profile": environment.policy_profile,
                    }
            capabilities = [item.model_schema() for item in registry.resolve(runtime_type, permissions)]
            self._step(db, state, "resolve_capabilities", {"count": len(capabilities), "runtime": runtime_type})
            db.commit()
        return {"context": context, "capabilities": capabilities, "evidence": [], "tool_call_count": 0, "step_count": 1, "status": "running"}

    def decide(self, state: AgentState) -> dict:
        settings = get_settings()
        with SessionLocal() as db:
            run_state = db.get(AgentRun, state["run_id"])
            if run_state and run_state.status == "cancelled":
                return {"decision": {}, "pending_calls": [], "answer": "本次处理已取消。", "status": "cancelled"}
            if run_state and run_state.started_at and (datetime.now(timezone.utc) - run_state.started_at).total_seconds() >= settings.agent_timeout_seconds:
                run_state.status = "failed"; run_state.error_code = "RUN_TIMEOUT"; db.commit()
                return {"decision": {}, "pending_calls": [], "answer": "本次处理已超过允许的总时长，已安全停止。", "status": "failed"}
        if state.get("step_count", 0) >= settings.agent_max_steps or state.get("tool_call_count", 0) >= settings.agent_max_tool_calls:
            return {"decision": {}, "pending_calls": [], "answer": self._limit_answer(state), "status": "completed"}
        with SessionLocal() as db:
            run = db.get(AgentRun, state["run_id"])
            try:
                decision = self.gateway.decide(
                    db,
                    run_id=state["run_id"],
                    question=state["question"],
                    history=state.get("history", []),
                    context=state.get("context", {}),
                    capabilities=state.get("capabilities", []),
                    evidence=state.get("evidence", []),
                )
                payload = decision.model_dump(mode="json")
                run.request_json = payload["request"]
                run.plan_json = {"tool_calls": payload["tool_calls"]}
                self._step(db, state, "decision", {"decision": decision.decision, "tool_calls": len(decision.tool_calls)})
                db.commit()
                answer = decision.answer if decision.decision == "respond" else decision.clarification_question if decision.decision == "clarify" else ""
                return {
                    "decision": payload,
                    "pending_calls": payload["tool_calls"],
                    "answer": answer or "",
                    "step_count": state.get("step_count", 0) + 1,
                }
            except Exception as exc:  # noqa: BLE001
                run.status = "failed"
                run.error_code = "DECISION_FAILED"
                run.error_message = str(exc)[:2000]
                db.commit()
                return {
                    "decision": {},
                    "pending_calls": [],
                    "answer": "暂时无法可靠理解并处理这个请求，请补充具体目标后重试。",
                    "status": "failed",
                    "error": str(exc)[:1000],
                }

    def prepare_actions(self, state: AgentState) -> dict:
        action_ids: list[str] = []
        observations: list[dict] = []
        requires_approval = False
        precheck_calls = 0
        with SessionLocal() as db:
            for call in state.get("pending_calls", []):
                definition = registry.get(call["capability"])
                if not definition or not any(item["name"] == call["capability"] for item in state.get("capabilities", [])):
                    observations.append({"capability": call["capability"], "status": "denied", "summary": "Capability is unavailable or not registered"})
                    continue
                decision_kind = state.get("decision", {}).get("decision")
                if definition.effect == "change" and decision_kind != "propose_change":
                    observations.append({"capability": definition.name, "status": "denied", "summary": "State-changing capabilities require an explicit propose_change decision"})
                    continue
                try:
                    arguments = definition.validate_arguments(call.get("arguments") or {})
                except Exception as exc:  # noqa: BLE001
                    observations.append({"capability": definition.name, "status": "denied", "summary": str(exc)})
                    continue
                target = {"name": arguments.get("service") or arguments.get("entity") or arguments.get("endpoint") or arguments.get("deployment") or arguments.get("change")}
                snapshot = {
                    "capability": definition.name,
                    "version": definition.version,
                    "project_id": state.get("project_id"),
                    "environment_id": state.get("environment_id"),
                    "target": target,
                    "arguments": arguments,
                    "effect": definition.effect,
                }
                action = Action(
                    id=str(uuid4()),
                    run_id=state["run_id"],
                    capability_name=definition.name,
                    capability_version=definition.version,
                    project_id=state.get("project_id"),
                    environment_id=state.get("environment_id"),
                    target_json=target,
                    arguments_json=arguments,
                    purpose=call.get("purpose"),
                    effect=definition.effect,
                    action_hash=compute_action_hash(snapshot),
                    status="proposed",
                )
                db.add(action)
                db.flush()
                policy = self.policy.evaluate(db, action, definition, state["user_id"])
                db.add(PolicyDecision(action_id=action.id, decision=policy.decision, risk_level=policy.risk_level, reason_code=policy.reason_code, reason=policy.reason, matched_policies_json=policy.matched_policies))
                action_ids.append(action.id)
                if policy.decision == "deny":
                    action.status = "denied"
                    observations.append({"action_id": action.id, "capability": definition.name, "status": "denied", "summary": policy.reason})
                elif policy.decision == "clarify":
                    action.status = "needs_clarification"
                    observations.append({"action_id": action.id, "capability": definition.name, "status": "clarify", "summary": policy.reason})
                elif policy.decision == "require_approval":
                    precheck = self._run_precheck(db, state, action, definition)
                    precheck_calls += 1
                    observations.append(precheck)
                    if not self._precheck_satisfied(action, precheck):
                        action.status = "precheck_failed"
                        observations.append({"action_id": action.id, "capability": definition.name, "status": "denied", "summary": "变更前置检查失败，未创建审批。"})
                    else:
                        action.status = "waiting_for_approval"
                        requires_approval = True
                        project = db.get(Project, action.project_id)
                        db.add(
                            Approval(
                                id=str(uuid4()),
                                action_id=action.id,
                                action_hash=action.action_hash,
                                requested_from=project.owner_id,
                                impact_summary=f"执行 {definition.name}，目标 {target.get('name') or '当前环境'}；前置检查 {definition.precheck} 已通过；执行后使用 {definition.verifier} 验证",
                                risk_summary=policy.reason + (f"；可用回滚能力：{definition.rollback}" if definition.rollback else "；无自动回滚能力"),
                                expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
                            )
                        )
                else:
                    action.status = "ready"
                append_audit_event(db, actor_type="agent", actor_id=state["run_id"], event_type="action.prepared", payload={"capability": definition.name, "policy": policy.decision, "action_hash": action.action_hash}, project_id=action.project_id, environment_id=action.environment_id, run_id=action.run_id, action_id=action.id)
            run = db.get(AgentRun, state["run_id"])
            run.status = "waiting_for_approval" if requires_approval else "running"
            self._step(db, state, "policy", {"actions": action_ids, "requires_approval": requires_approval})
            db.commit()
        return {"action_ids": action_ids, "evidence": state.get("evidence", []) + observations, "tool_call_count": state.get("tool_call_count", 0) + precheck_calls, "status": "waiting_for_approval" if requires_approval else "running"}

    def await_approval(self, state: AgentState) -> dict:
        with SessionLocal() as db:
            approvals = list(db.scalars(select(Approval).join(Action).where(Action.id.in_(state.get("action_ids", [])), Approval.decision == "pending")))
            payload = [
                {"approval_id": item.id, "action_id": item.action_id, "action_hash": item.action_hash, "impact": item.impact_summary, "risk": item.risk_summary, "expires_at": item.expires_at.isoformat()}
                for item in approvals
            ]
        interrupt({"type": "approval_required", "approvals": payload})
        with SessionLocal() as db:
            decided = list(db.scalars(select(Approval).join(Action).where(Action.id.in_(state.get("action_ids", [])))))
            if any(item.decision == "rejected" for item in decided):
                return {"status": "completed", "answer": "该变更请求已被拒绝，没有执行任何状态变更。"}
            if any(item.decision != "approved" for item in decided):
                return {"status": "completed", "answer": "审批未通过或已经失效，没有执行任何状态变更。"}
        return {"status": "running"}

    def execute(self, state: AgentState) -> dict:
        observations = list(state.get("evidence", []))
        executed_calls = 0
        with SessionLocal() as db:
            actions = list(db.scalars(select(Action).where(Action.id.in_(state.get("action_ids", []))).order_by(Action.created_at)))
            for action in actions:
                run_state = db.get(AgentRun, state["run_id"])
                if run_state and run_state.status == "cancelled":
                    observations.append({"action_id": action.id, "status": "cancelled", "summary": "Run was cancelled before execution"})
                    break
                definition = registry.get(action.capability_name)
                if not definition or action.status in {"denied", "rejected", "expired", "precheck_failed", "cancelled", "needs_clarification"}:
                    continue
                if action.effect == "change":
                    approval = db.scalar(select(Approval).where(Approval.action_id == action.id))
                    snapshot = {"capability": action.capability_name, "version": action.capability_version, "project_id": action.project_id, "environment_id": action.environment_id, "target": action.target_json, "arguments": action.arguments_json, "effect": action.effect}
                    if not approval or approval.decision != "approved" or approval.expires_at <= datetime.now(timezone.utc) or approval.action_hash != compute_action_hash(snapshot):
                        action.status = "approval_invalid"
                        observations.append({"action_id": action.id, "status": "denied", "summary": "Approval is missing, expired or no longer matches the action"})
                        continue
                    renewed = self.policy.evaluate(db, action, definition, state["user_id"])
                    if renewed.decision != "require_approval":
                        action.status = "needs_clarification" if renewed.decision == "clarify" else "denied"
                        observations.append({"action_id": action.id, "status": "denied", "summary": renewed.reason})
                        continue
                    precheck = self._run_precheck(db, state, action, definition)
                    executed_calls += 1
                    precheck["recheck_for"] = action.id
                    observations.append(precheck)
                    if not self._precheck_satisfied(action, precheck):
                        action.status = "precheck_changed"
                        observations.append({"action_id": action.id, "status": "denied", "summary": "执行前复核未通过，未执行状态变更。"})
                        continue
                action.status = "running"
                observation = self.executor.execute(db, action, definition)
                executed_calls += 1
                observations.append(observation)
                if action.effect == "change" and observation["status"] == "success" and definition.verifier:
                    verifier = registry.get(definition.verifier)
                    verify_arguments = {name: action.arguments_json[name] for name in verifier.arguments if name in action.arguments_json}
                    verify_snapshot = {"capability": verifier.name, "version": verifier.version, "project_id": action.project_id, "environment_id": action.environment_id, "target": action.target_json, "arguments": verify_arguments, "effect": "read"}
                    verify_action = Action(id=str(uuid4()), run_id=action.run_id, capability_name=verifier.name, capability_version=verifier.version, project_id=action.project_id, environment_id=action.environment_id, target_json=action.target_json, arguments_json=verify_arguments, purpose="Post-change verification", effect="read", action_hash=compute_action_hash(verify_snapshot), status="running")
                    db.add(verify_action)
                    db.flush()
                    verify_policy = self.policy.evaluate(db, verify_action, verifier, state["user_id"])
                    db.add(PolicyDecision(action_id=verify_action.id, decision=verify_policy.decision, risk_level=verify_policy.risk_level, reason_code=verify_policy.reason_code, reason=verify_policy.reason, matched_policies_json=verify_policy.matched_policies))
                    if verify_policy.decision != "allow":
                        verify_action.status = "denied"
                        verification = {"capability": verifier.name, "status": "denied", "summary": verify_policy.reason}
                    else:
                        verification = self.executor.execute(db, verify_action, verifier)
                        executed_calls += 1
                    verification["verification_for"] = action.id
                    observations.append(verification)
                    action.status = "verified" if self._verification_satisfied(action, verification) else "verification_failed"
                    if action.status == "verification_failed" and definition.rollback:
                        observations.append({"action_id": action.id, "status": "rollback_available", "summary": f"验证未通过；可提出受控回滚 {definition.rollback}，回滚仍需重新经过策略与审批。", "rollback_capability": definition.rollback})
                append_audit_event(db, actor_type="agent", actor_id=state["run_id"], event_type="action.executed", payload={"status": action.status}, project_id=action.project_id, environment_id=action.environment_id, run_id=action.run_id, action_id=action.id)
            self._step(db, state, "execute", {"observations": len(observations)})
            db.commit()
        return {"evidence": observations, "tool_call_count": state.get("tool_call_count", 0) + executed_calls, "action_ids": [], "pending_calls": [], "step_count": state.get("step_count", 0) + 1, "status": "running"}

    def finish(self, state: AgentState) -> dict:
        answer = state.get("answer") or "目前没有足够信息形成可靠回答，请补充目标或选择项目环境后重试。"
        with SessionLocal() as db:
            run = db.get(AgentRun, state["run_id"])
            terminal_status = state.get("status") if state.get("status") in {"failed", "cancelled"} else "completed"
            run.status = terminal_status
            run.completed_at = datetime.now(timezone.utc)
            run.current_step = "finish"
            self._step(db, state, "finish", {"answer_length": len(answer)})
            db.commit()
        return {"answer": answer, "status": terminal_status}

    @staticmethod
    def route_decision(state: AgentState) -> str:
        if state.get("status") in {"failed", "cancelled", "completed"}:
            return "finish"
        return "prepare" if state.get("decision", {}).get("decision") in {"invoke_tools", "propose_change"} else "finish"

    @staticmethod
    def route_prepared(state: AgentState) -> str:
        if state.get("status") == "waiting_for_approval":
            return "approval"
        if any(action for action in state.get("action_ids", [])):
            return "execute"
        if state.get("evidence"):
            return "decide"
        return "finish"

    @staticmethod
    def route_approval(state: AgentState) -> str:
        with SessionLocal() as db:
            approvals = list(db.scalars(select(Approval).join(Action).where(Action.id.in_(state.get("action_ids", [])))))
            approved = approvals and all(item.decision == "approved" for item in approvals)
        return "execute" if approved else "finish"

    @staticmethod
    def route_after_execute(state: AgentState) -> str:
        return "decide"

    @staticmethod
    def _limit_answer(state: AgentState) -> str:
        evidence = state.get("evidence", [])
        if evidence:
            lines = [f"- {item.get('summary', '已完成一步调查')}" for item in evidence[-6:]]
            return "调查已达到本轮步骤上限。当前已取得的信息：\n\n" + "\n".join(lines) + "\n\n仍需更多证据时，请缩小问题范围后继续。"
        return "当前请求已达到处理步骤上限，但尚未取得足够证据。请缩小问题范围后重试。"

    @staticmethod
    def _verification_satisfied(action: Action, observation: dict) -> bool:
        if observation.get("status") != "success":
            return False
        data = observation.get("data") or {}
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":")).lower()
        records = OpsAgentGraph._runtime_records(data)
        if action.capability_name == "service.stop":
            if "activestate=inactive" in text:
                return True
            if records:
                return all(str(item.get("State") or item.get("state") or "").lower() in {"exited", "stopped", "dead"} for item in records)
            return '"replicas":0' in text
        if action.capability_name in {"service.start", "service.restart"}:
            if "activestate=active" in text:
                return True
            if records and all(str(item.get("State") or item.get("state") or "").lower() == "running" for item in records):
                return True
            deployment = records[0] if records else {}
            desired = int((deployment.get("spec") or {}).get("replicas") or 0) if isinstance(deployment, dict) else 0
            available = int((deployment.get("status") or {}).get("availableReplicas") or 0) if isinstance(deployment, dict) else 0
            return desired > 0 and available >= desired
        if action.capability_name == "service.scale":
            replicas = action.arguments_json.get("replicas")
            if records and isinstance(records[0], dict) and isinstance(records[0].get("spec"), dict):
                desired = int(records[0]["spec"].get("replicas") or 0)
                available = int((records[0].get("status") or {}).get("availableReplicas") or 0)
                return desired == replicas and available == replicas
            running = [item for item in records if str(item.get("State") or item.get("state") or "").lower() == "running"]
            return len(running) == replicas and len(records) == replicas
        return True

    @staticmethod
    def _precheck_satisfied(action: Action, observation: dict) -> bool:
        if observation.get("status") != "success":
            return False
        if action.capability_name == "service.restart":
            return bool(OpsAgentGraph._runtime_records(observation.get("data") or {})) or "activestate=" in json.dumps(observation.get("data") or {}).lower()
        return True

    @staticmethod
    def _runtime_records(data: dict) -> list[dict]:
        raw = data.get("stdout") if isinstance(data, dict) else None
        if not isinstance(raw, str) or not raw.strip():
            return []
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else [parsed] if isinstance(parsed, dict) else []
        except json.JSONDecodeError:
            records: list[dict] = []
            for line in raw.splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    records.append(item)
            return records

    def _run_precheck(self, db, state: AgentState, change_action: Action, change_definition) -> dict:
        precheck_name = change_definition.precheck
        if not precheck_name:
            return {"action_id": change_action.id, "capability": "precheck", "status": "failed", "summary": "No registered precheck is available"}
        definition = registry.get(precheck_name)
        arguments = {name: change_action.arguments_json[name] for name in definition.arguments if name in change_action.arguments_json}
        snapshot = {"capability": definition.name, "version": definition.version, "project_id": change_action.project_id, "environment_id": change_action.environment_id, "target": change_action.target_json, "arguments": arguments, "effect": "read"}
        precheck_action = Action(id=str(uuid4()), run_id=change_action.run_id, capability_name=definition.name, capability_version=definition.version, project_id=change_action.project_id, environment_id=change_action.environment_id, target_json=change_action.target_json, arguments_json=arguments, purpose=f"Precheck for {change_action.capability_name}", effect="read", action_hash=compute_action_hash(snapshot), status="running")
        db.add(precheck_action); db.flush()
        policy = self.policy.evaluate(db, precheck_action, definition, state["user_id"])
        db.add(PolicyDecision(action_id=precheck_action.id, decision=policy.decision, risk_level=policy.risk_level, reason_code=policy.reason_code, reason=policy.reason, matched_policies_json=policy.matched_policies))
        if policy.decision != "allow":
            precheck_action.status = "denied"
            return {"action_id": precheck_action.id, "capability": definition.name, "status": "denied", "summary": policy.reason}
        return self.executor.execute(db, precheck_action, definition)

    @staticmethod
    def _step(db, state: AgentState, step_type: str, output: dict) -> None:
        run = db.get(AgentRun, state["run_id"])
        sequence = (run.step_count if run else 0) + 1
        if run:
            run.step_count = sequence
            run.current_step = step_type
        db.add(AgentStep(run_id=state["run_id"], sequence=sequence, step_type=step_type, status="success", output_summary_json=output, finished_at=datetime.now(timezone.utc)))
