from datetime import datetime, timedelta, timezone
import json
from typing import Any
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import select, update

from app.agent.state import AgentState
from app.agent.status import TERMINAL_RUN_STATUSES, close_pending_approval_batch
from app.audit.service import append_audit_event
from app.capabilities.registry import registry
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.llm.gateway import LLMGateway, StructuredDecisionError
from app.models.action import Action, Approval, PolicyDecision
from app.models.agent import AgentRun, AgentStep
from app.models.monitoring import MonitorEvent
from app.models.project import Connection, Environment, Project, ProjectMember
from app.policy.action_hash import action_snapshot, compute_action_hash, configuration_revision
from app.policy.engine import PolicyEngine, permissions_for_role
from app.runtime.executor import RuntimeExecutor
from app.runtime.verification import runtime_records, verification_satisfied


def approval_summaries(capability_name: str, target: dict[str, Any], rollback: dict[str, Any] | None) -> tuple[str, str]:
    name = str(target.get("name") or "当前目标")
    verbs = {
        "service.restart": "重启服务",
        "service.start": "启动服务",
        "service.stop": "停止服务",
        "service.scale": "调整服务副本",
        "config.update_registered": "修改已登记配置",
        "deployment.apply_registered": "执行已登记部署",
    }
    verb = verbs.get(capability_name, "执行变更")
    impact = f"Agent 准备{verb} {name}。批准后会执行这次变更，并在执行后再次检查目标状态。"
    if capability_name == "service.restart":
        risk = f"重启期间 {name} 可能短暂不可用，正在处理的请求可能中断。"
    elif capability_name == "service.stop":
        risk = f"停止后 {name} 将不可用，依赖它的功能可能受到影响。"
    elif capability_name == "service.start":
        risk = f"启动 {name} 会改变当前运行状态，如果配置或依赖异常，服务可能启动失败。"
    elif capability_name == "service.scale":
        risk = f"调整 {name} 的副本数会改变承载能力，副本过少可能影响可用性，副本过多可能增加资源占用。"
    else:
        risk = f"{verb}会改变当前环境状态，可能影响相关服务的稳定性。"
    rollback_kind = str((rollback or {}).get("kind") or "")
    if rollback_kind == "capability":
        rollback_text = {
            "service.start": "重新启动服务",
            "service.stop": "停止本次启动的服务",
            "service.scale": "恢复原有副本数量",
        }.get(str(rollback.get("capability")), "执行预设恢复动作")
        risk += f" 如果执行后的状态检查未通过，系统会尝试{rollback_text}。"
    elif rollback_kind == "config_backup":
        risk += " 如果新配置校验未通过，系统会尝试恢复变更前的配置文件。"
    elif rollback_kind == "deployment":
        risk += " 如果部署后的状态检查未通过，系统会按已登记的部署恢复方案处理。"
    elif rollback_kind == "no_op":
        risk += " 目标在执行前已经处于所需状态，因此异常时无需反向改变原状态。"
    else:
        risk += " 当前没有可自动执行的恢复步骤；异常时系统会停止后续动作并保留诊断证据。"
    return impact, risk


def resolve_action_spec(environment: Environment, definition, arguments: dict[str, Any], operation_id: str, connection: Connection | None = None) -> dict[str, Any]:
    config = environment.config_json or {}
    resolved: dict[str, Any] = {
        "runtime_type": environment.runtime_type,
        "workdir": environment.workdir,
        "namespace": environment.namespace,
        "connection_id": environment.connection_id,
        "compose_file": str(config.get("compose_file") or "docker-compose.yml"),
        "operation_id": operation_id,
        "configuration_revision": configuration_revision(environment, connection),
        "capability_bindings": registry.related_bindings(definition),
    }
    if connection:
        resolved["connection"] = {
            "id": connection.id,
            "connection_type": connection.connection_type,
            "host": connection.host,
            "port": connection.port,
            "username": connection.username,
            "credential_ref": connection.credential_ref,
            "host_fingerprint": connection.host_fingerprint,
        }
    if definition.executor == "registered_deployment":
        key = arguments.get("deployment")
        spec = (config.get("registered_deployments") or {}).get(key)
        if not isinstance(spec, dict):
            raise ValueError(f"Deployment recipe is not registered: {key}")
        if definition.name == "deployment.apply_registered" and environment.runtime_type == "docker_compose" and spec.get("rollback") not in {"stop", "restart"}:
            raise ValueError("Docker registered deployments require rollback=stop or rollback=restart")
        resolved["registered_deployment"] = json.loads(json.dumps(spec))
    elif definition.executor == "registered_config":
        key = arguments.get("change")
        spec = (config.get("registered_config_changes") or {}).get(key)
        if not isinstance(spec, dict):
            raise ValueError(f"Configuration change is not registered: {key}")
        if not spec.get("current_sha256") and not spec.get("allow_create"):
            raise ValueError("Registered configuration updates require current_sha256 or explicit allow_create")
        resolved["registered_config_change"] = json.loads(json.dumps(spec))
        resolved["backup_path"] = f"{spec.get('path')}.ops-agent.backup.{operation_id}"
    return resolved


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
            definitions = registry.resolve(runtime_type, permissions)
            if state.get("read_only"):
                definitions = [item for item in definitions if item.effect == "read"]
            context["execution_mode"] = state.get("execution_mode") or "interactive"
            context["read_only"] = bool(state.get("read_only"))
            if state.get("monitor_event_id"):
                context["monitor_event_id"] = state["monitor_event_id"]
            capabilities = [item.model_schema() for item in definitions]
            self._step(db, state, "resolve_capabilities", {"count": len(capabilities), "runtime": runtime_type})
            db.commit()
        return {"context": context, "capabilities": capabilities, "evidence": [], "tool_call_count": 0, "step_count": 1, "status": "running"}

    def decide(self, state: AgentState) -> dict:
        settings = get_settings()
        with SessionLocal() as db:
            run_state = db.get(AgentRun, state["run_id"])
            if run_state and run_state.status in TERMINAL_RUN_STATUSES:
                if run_state.status == "cancelled":
                    answer = "本次处理已取消。"
                elif run_state.status == "failed":
                    answer = "本次处理已由恢复流程安全终止，晚到结果不会被采用。"
                else:
                    answer = "本次处理已经完成。"
                return {"decision": {}, "pending_calls": [], "answer": answer, "status": run_state.status}
            if run_state and run_state.started_at and (datetime.now(timezone.utc) - run_state.started_at).total_seconds() >= settings.agent_timeout_seconds:
                timed_out = db.scalar(
                    update(AgentRun)
                    .where(
                        AgentRun.id == run_state.id,
                        AgentRun.status == "running",
                        AgentRun.cancel_requested_at.is_(None),
                    )
                    .values(status="failed", error_code="RUN_TIMEOUT")
                    .returning(AgentRun.id)
                )
                db.commit()
                if timed_out:
                    return {"decision": {}, "pending_calls": [], "answer": "本次处理已超过允许的总时长，已安全停止。", "status": "failed"}
                db.refresh(run_state)
                if run_state.status == "cancelled" or run_state.cancel_requested_at:
                    return {"decision": {}, "pending_calls": [], "answer": "本次处理已取消。", "status": "cancelled"}
                return {"decision": {}, "pending_calls": [], "answer": "本次处理已由其他恢复流程安全终止。", "status": "failed"}
        with SessionLocal() as db:
            run = db.get(AgentRun, state["run_id"])
            bootstrap_calls = self._monitor_diagnostic_calls(db, state)
            if bootstrap_calls:
                request = {
                    "goal": "investigate",
                    "scope": "runtime",
                    "time_focus": "current",
                    "requested_effect": "read",
                    "subjects": [],
                    "desired_output": "diagnosis",
                    "constraints": ["automatic read-only diagnosis"],
                    "confidence": 1.0,
                    "summary": "自动收集严重巡检事件的实时状态和日志",
                }
                controls = {
                    key: run.request_json[key]
                    for key in ("source", "execution_mode", "read_only", "monitor_event_id")
                    if isinstance(run.request_json, dict) and key in run.request_json
                }
                payload = {
                    "decision": "invoke_tools",
                    "request": request,
                    "tool_calls": bootstrap_calls,
                    "answer": None,
                    "clarification_question": None,
                    "claims": [],
                }
                run.request_json = {**request, **controls}
                run.plan_json = {"tool_calls": bootstrap_calls, "source": "monitor_diagnostic_bootstrap"}
                self._step(
                    db,
                    state,
                    "decision",
                    {"decision": "invoke_tools", "tool_calls": len(bootstrap_calls), "source": "monitor_diagnostic_bootstrap"},
                )
                db.commit()
                return {
                    "decision": payload,
                    "pending_calls": bootstrap_calls,
                    "answer": "",
                    "claims": [],
                    "step_count": state.get("step_count", 0) + 1,
                }
            try:
                decision = self.gateway.decide(
                    db,
                    run_id=state["run_id"],
                    question=state["question"],
                    history=state.get("history", []),
                    context=state.get("context", {}),
                    capabilities=state.get("capabilities", []),
                    evidence=state.get("evidence", []),
                    cancel_check=lambda: self._run_cancelled(state["run_id"]),
                )
                payload = decision.model_dump(mode="json")
                controls = {
                    key: run.request_json[key]
                    for key in ("source", "execution_mode", "read_only", "monitor_event_id")
                    if isinstance(run.request_json, dict) and key in run.request_json
                }
                run.request_json = {**payload["request"], **controls}
                run.plan_json = {"tool_calls": payload["tool_calls"]}
                self._step(db, state, "decision", {"decision": decision.decision, "tool_calls": len(decision.tool_calls)})
                db.commit()
                answer = decision.answer if decision.decision == "respond" else decision.clarification_question if decision.decision == "clarify" else ""
                return {
                    "decision": payload,
                    "pending_calls": payload["tool_calls"],
                    "answer": answer or "",
                    "claims": payload.get("claims") or [],
                    "step_count": state.get("step_count", 0) + 1,
                }
            except Exception as exc:  # noqa: BLE001
                db.refresh(run)
                if run.status == "cancelled" or run.cancel_requested_at:
                    self._step(db, state, "decision", {"status": "cancelled"}, status="cancelled")
                    db.commit()
                    return {
                        "decision": {},
                        "pending_calls": [],
                        "answer": "本次处理已取消。",
                        "status": "cancelled",
                    }
                invalid_decision = isinstance(exc, StructuredDecisionError)
                failure_code = "DECISION_INVALID" if invalid_decision else "MODEL_CALL_FAILED"
                failed = db.scalar(
                    update(AgentRun)
                    .where(
                        AgentRun.id == run.id,
                        AgentRun.status == "running",
                        AgentRun.cancel_requested_at.is_(None),
                    )
                    .values(status="failed", error_code=failure_code, error_message=str(exc)[:2000])
                    .returning(AgentRun.id)
                )
                if not failed:
                    db.rollback()
                    current = db.get(AgentRun, run.id)
                    cancelled = bool(current and (current.status == "cancelled" or current.cancel_requested_at))
                    return {
                        "decision": {},
                        "pending_calls": [],
                        "answer": "本次处理已取消。" if cancelled else "本次处理已由其他恢复流程安全终止。",
                        "status": "cancelled" if cancelled else "failed",
                    }
                self._step(
                    db,
                    state,
                    "decision",
                    {"error": "Structured model decision was invalid" if invalid_decision else "Model call failed"},
                    status="failed",
                    error_code=failure_code,
                )
                db.commit()
                return {
                    "decision": {},
                    "pending_calls": [],
                    "answer": (
                        "模型未能生成符合安全规范的执行计划，因此没有执行新的变更。已完成的只读检查结果仍已保留，请重新提交该请求。"
                        if invalid_decision
                        else "模型服务本次调用失败，因此没有执行新的变更。已完成的只读检查结果仍已保留，请稍后重试。"
                    ),
                    "status": "failed",
                    "error": str(exc)[:1000],
                }

    @staticmethod
    def _run_cancelled(run_id: str) -> bool:
        with SessionLocal() as db:
            run = db.get(AgentRun, run_id)
            return bool(not run or run.status in TERMINAL_RUN_STATUSES or run.cancel_requested_at)

    @staticmethod
    def _monitor_diagnostic_calls(db, state: AgentState) -> list[dict[str, Any]]:
        if (
            state.get("execution_mode") != "monitor_diagnosis"
            or not state.get("read_only")
            or state.get("evidence")
            or not state.get("monitor_event_id")
        ):
            return []
        event = db.get(MonitorEvent, state["monitor_event_id"])
        if not event:
            return []
        available = {str(item.get("name")) for item in state.get("capabilities", [])}
        if event.service_name == "__environment__":
            return (
                [{"capability": "service.list", "arguments": {}, "purpose": "复核当前环境的服务状态"}]
                if "service.list" in available
                else []
            )
        calls = []
        if "service.status" in available:
            calls.append({
                "capability": "service.status",
                "arguments": {"service": event.service_name},
                "purpose": f"复核 {event.service_name} 的实时状态",
            })
        if "service.logs" in available:
            calls.append({
                "capability": "service.logs",
                "arguments": {"service": event.service_name, "tail": 100},
                "purpose": f"读取 {event.service_name} 的最近 100 行日志",
            })
        return calls

    def prepare_actions(self, state: AgentState) -> dict:
        settings = get_settings()
        action_ids: list[str] = []
        observations: list[dict] = []
        requires_approval = False
        precheck_calls = 0
        seen_calls: set[str] = set()
        with SessionLocal() as db:
            for call in state.get("pending_calls", []):
                call_key: str | None = None
                definition = registry.get(call["capability"])
                if not definition or not any(item["name"] == call["capability"] for item in state.get("capabilities", [])):
                    observations.append({"capability": call["capability"], "status": "denied", "summary": "Capability is unavailable or not registered"})
                    continue
                if state.get("read_only") and definition.effect != "read":
                    observations.append({"capability": definition.name, "status": "denied", "summary": "Automatic monitor diagnosis is strictly read-only"})
                    continue
                decision_kind = state.get("decision", {}).get("decision")
                if definition.effect == "change" and decision_kind != "propose_change":
                    observations.append({"capability": definition.name, "status": "denied", "summary": "State-changing capabilities require an explicit propose_change decision"})
                    continue
                try:
                    action_id = str(uuid4())
                    arguments = definition.validate_arguments(call.get("arguments") or {})
                    call_key = json.dumps(
                        {"capability": definition.name, "arguments": arguments},
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    if call_key in seen_calls:
                        observations.append({
                            "capability": definition.name,
                            "status": "denied",
                            "summary": "Duplicate capability call was removed from this decision",
                        })
                        continue
                    seen_calls.add(call_key)
                    environment = db.get(Environment, state.get("environment_id"))
                    if not environment:
                        raise ValueError("Environment is missing")
                    connection = db.get(Connection, environment.connection_id) if environment.connection_id else None
                    resolved_spec = resolve_action_spec(environment, definition, arguments, action_id, connection)
                except Exception as exc:  # noqa: BLE001
                    if call_key:
                        seen_calls.discard(call_key)
                    observations.append({"capability": definition.name, "status": "denied", "summary": str(exc)})
                    continue
                target = {"name": arguments.get("service") or arguments.get("entity") or arguments.get("endpoint") or arguments.get("deployment") or arguments.get("change")}
                action = Action(
                    id=action_id,
                    run_id=state["run_id"],
                    capability_name=definition.name,
                    capability_version=definition.version,
                    capability_definition_hash=registry.definition_hash(definition.name, definition.version),
                    risk_level=definition.risk_level,
                    approval_mode=definition.approval_mode,
                    policy_version=self.policy.policy_version,
                    config_revision=resolved_spec["configuration_revision"],
                    project_id=state.get("project_id"),
                    environment_id=state.get("environment_id"),
                    target_json=target,
                    arguments_json=arguments,
                    resolved_spec_json=resolved_spec,
                    rollback_spec_json={},
                    purpose=call.get("purpose"),
                    effect=definition.effect,
                    action_hash="",
                    status="proposed",
                )
                action.action_hash = compute_action_hash(action_snapshot(action))
                db.add(action)
                db.flush()
                policy = self.policy.evaluate(db, action, definition, state["user_id"])
                action.risk_level = policy.risk_level
                action.action_hash = compute_action_hash(action_snapshot(action))
                db.add(PolicyDecision(action_id=action.id, decision=policy.decision, risk_level=policy.risk_level, reason_code=policy.reason_code, reason=policy.reason, matched_policies_json=policy.matched_policies, policy_version=self.policy.policy_version))
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
                        rollback_spec = self._build_rollback_spec(action, definition, precheck)
                        action.rollback_spec_json = self._policy_checked_rollback_spec(
                            db, state["user_id"], action, rollback_spec
                        )
                        action.action_hash = compute_action_hash(self._action_snapshot(action))
                        action.status = "waiting_for_approval"
                        requires_approval = True
                        project = db.get(Project, action.project_id)
                        db.add(
                            Approval(
                                id=str(uuid4()),
                                action_id=action.id,
                                action_hash=action.action_hash,
                                requested_from=project.owner_id,
                                impact_summary=approval_summaries(definition.name, target, action.rollback_spec_json)[0],
                                risk_summary=approval_summaries(definition.name, target, action.rollback_spec_json)[1],
                                expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
                            )
                        )
                else:
                    action.status = "ready"
                append_audit_event(db, actor_type="agent", actor_id=state["run_id"], event_type="action.prepared", payload={"capability": definition.name, "policy": policy.decision, "action_hash": action.action_hash}, project_id=action.project_id, environment_id=action.environment_id, run_id=action.run_id, action_id=action.id)
            run = db.get(AgentRun, state["run_id"])
            db.refresh(run)
            if run.status == "cancelled" or run.cancel_requested_at:
                close_pending_approval_batch(
                    db,
                    run.id,
                    decision="cancelled",
                    reason_code="RUN_CANCELLED",
                    comment="Run was cancelled while actions were being prepared",
                    action_status="cancelled",
                )
                db.execute(
                    update(Action)
                    .where(
                        Action.run_id == run.id,
                        Action.status.in_(["proposed", "ready", "waiting_for_approval", "approved"]),
                    )
                    .values(status="cancelled", execution_finished_at=datetime.now(timezone.utc))
                )
                self._step(db, state, "policy", {"actions": action_ids, "status": "cancelled"}, status="cancelled")
                db.commit()
                return {
                    "action_ids": [],
                    "pending_calls": [],
                    "evidence": state.get("evidence", []) + observations,
                    "tool_call_count": state.get("tool_call_count", 0) + precheck_calls,
                    "status": "cancelled",
                    "answer": "本次处理已取消。",
                }
            transitioned = db.scalar(
                update(AgentRun)
                .where(
                    AgentRun.id == run.id,
                    AgentRun.status == "running",
                    AgentRun.cancel_requested_at.is_(None),
                )
                .values(status="waiting_for_approval" if requires_approval else "running")
                .returning(AgentRun.id)
            )
            if not transitioned:
                db.refresh(run)
                cancelled = bool(run.status == "cancelled" or run.cancel_requested_at)
                close_pending_approval_batch(
                    db,
                    run.id,
                    decision="cancelled" if cancelled else "invalidated",
                    reason_code="RUN_CANCELLED" if cancelled else "RUN_NO_LONGER_EXECUTABLE",
                    comment="Run cancellation won the action preparation race" if cancelled else "Run was closed by recovery while actions were being prepared",
                    action_status="cancelled" if cancelled else "approval_invalid",
                )
                db.execute(
                    update(Action)
                    .where(
                        Action.run_id == run.id,
                        Action.status.in_(["proposed", "ready"]),
                    )
                    .values(status="cancelled" if cancelled else "failed", execution_finished_at=datetime.now(timezone.utc))
                )
                terminal_status = "cancelled" if cancelled else "failed"
                self._step(db, state, "policy", {"actions": action_ids, "status": terminal_status}, status=terminal_status)
                db.commit()
                return {
                    "action_ids": [],
                    "pending_calls": [],
                    "evidence": state.get("evidence", []) + observations,
                    "tool_call_count": state.get("tool_call_count", 0) + precheck_calls,
                    "status": terminal_status,
                    "answer": "本次处理已取消。" if cancelled else "本次处理已由其他恢复流程安全终止。",
                }
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
            decided = db.execute(
                select(Approval, Action)
                .join(Action)
                .where(Action.id.in_(state.get("action_ids", [])))
            ).all()
            approved = [(approval, action) for approval, action in decided if approval.decision == "approved" and action.status == "approved"]
            if approved:
                skipped = [
                    {
                        "action_id": action.id,
                        "capability": action.capability_name,
                        "status": "rejected",
                        "summary": "用户未勾选该变更，本次未执行。",
                    }
                    for approval, action in decided
                    if approval.decision == "rejected" and approval.reason_code == "USER_BATCH_NOT_SELECTED"
                ]
                return {"status": "running", "evidence": state.get("evidence", []) + skipped}
            if any(approval.decision not in {"approved", "rejected"} for approval, _ in decided):
                return {"status": "completed", "answer": "审批未通过或已经失效，没有执行任何状态变更。"}
        return {"status": "completed", "answer": "你没有批准任何变更，本次没有执行状态修改。"}

    def execute(self, state: AgentState) -> dict:
        observations = list(state.get("evidence", []))
        executed_calls = 0
        terminal_error: tuple[str, str] | None = None
        with SessionLocal() as db:
            action_ids = state.get("action_ids", [])
            action_rows = list(db.scalars(select(Action).where(Action.id.in_(action_ids))))
            actions_by_id = {item.id: item for item in action_rows}
            actions = [actions_by_id[action_id] for action_id in action_ids if action_id in actions_by_id]
            for action in actions:
                run_state = db.get(AgentRun, state["run_id"])
                if run_state:
                    db.refresh(run_state)
                if run_state and run_state.status in TERMINAL_RUN_STATUSES:
                    observations.append({
                        "action_id": action.id,
                        "status": run_state.status,
                        "summary": "Run reached a terminal state before this Action could execute",
                    })
                    break
                if action.status in {
                    "denied", "rejected", "expired", "precheck_failed", "cancelled", "needs_clarification",
                    "approval_invalid", "precheck_changed", "rollback_failed", "execution_unknown",
                    "running", "executing", "succeeded", "verified", "failed", "verification_failed", "rolled_back",
                }:
                    continue
                snapshot_matches = action.action_hash == compute_action_hash(self._action_snapshot(action))
                definition = registry.get_bound(
                    action.capability_name,
                    action.capability_version,
                    action.capability_definition_hash,
                )
                governance_matches = bool(
                    definition
                    and action.risk_level == definition.risk_level
                    and action.approval_mode == definition.approval_mode
                    and action.policy_version == self.policy.policy_version
                    and self._configuration_revision_matches(db, action)
                )
                if not snapshot_matches or not definition or not governance_matches or not self._action_bindings_available(action):
                    action.status = "approval_invalid" if action.effect == "change" else "failed"
                    observations.append({
                        "action_id": action.id,
                        "capability": action.capability_name,
                        "status": "denied",
                        "summary": "Capability binding or Action snapshot no longer matches the approved definition",
                    })
                    append_audit_event(
                        db,
                        actor_type="agent",
                        actor_id=state["run_id"],
                        event_type="action.binding_invalid",
                        payload={"status": action.status, "capability": action.capability_name, "version": action.capability_version},
                        project_id=action.project_id,
                        environment_id=action.environment_id,
                        run_id=action.run_id,
                        action_id=action.id,
                    )
                    continue
                if action.effect == "change":
                    approval = db.scalar(select(Approval).where(Approval.action_id == action.id))
                    snapshot = self._action_snapshot(action)
                    if not approval or approval.decision != "approved" or approval.expires_at <= datetime.now(timezone.utc) or approval.action_hash != compute_action_hash(snapshot):
                        action.status = "approval_invalid"
                        observations.append({"action_id": action.id, "status": "denied", "summary": "Approval is missing, expired or no longer matches the action"})
                        continue
                    renewed = self.policy.evaluate(db, action, definition, state["user_id"])
                    if renewed.decision != "require_approval" or renewed.risk_level != action.risk_level:
                        action.status = "needs_clarification" if renewed.decision == "clarify" else "denied"
                        observations.append({"action_id": action.id, "status": "denied", "summary": renewed.reason})
                        continue
                    precheck = self._run_precheck(db, state, action, definition)
                    executed_calls += 1
                    precheck["recheck_for"] = action.id
                    observations.append(precheck)
                    db.expire_all()
                    run_after_precheck = db.get(AgentRun, state["run_id"])
                    if run_after_precheck and run_after_precheck.status in TERMINAL_RUN_STATUSES:
                        observations.append({
                            "action_id": action.id,
                            "status": run_after_precheck.status,
                            "summary": "Run reached a terminal state while the precheck was executing",
                        })
                        break
                    if not self._precheck_satisfied(action, precheck):
                        action.status = "precheck_changed"
                        observations.append({"action_id": action.id, "status": "denied", "summary": "执行前复核未通过，未执行状态变更。"})
                        continue
                expected_status = "approved" if action.effect == "change" else "ready"
                execution_token = str(uuid4())
                claimed = db.scalar(
                    update(Action)
                    .where(Action.id == action.id, Action.status == expected_status)
                    .values(status="executing", execution_token=execution_token, execution_started_at=datetime.now(timezone.utc))
                    .returning(Action.id)
                )
                if not claimed:
                    continue
                if action.effect == "change":
                    consumed = db.scalar(
                        update(Approval)
                        .where(Approval.action_id == action.id, Approval.decision == "approved", Approval.consumed_at.is_(None))
                        .values(consumed_at=datetime.now(timezone.utc))
                        .returning(Approval.id)
                    )
                    if not consumed:
                        db.rollback()
                        action = db.get(Action, action.id)
                        if action and action.status == "approved":
                            action.status = "approval_invalid"
                            db.commit()
                        observations.append({
                            "action_id": action.id if action else claimed,
                            "status": "denied",
                            "summary": "Approval was already consumed or is no longer executable",
                        })
                        continue
                db.commit()
                action = db.get(Action, action.id)
                try:
                    observation = self.executor.execute(db, action, definition)
                except Exception as exc:  # noqa: BLE001
                    if not self._execution_owned(db, action.id, execution_token):
                        db.rollback()
                        observations.append({
                            "action_id": action.id,
                            "status": "execution_unknown",
                            "summary": "Late execution failure was ignored after the Worker lost its lease",
                        })
                        continue
                    action.status = "failed"
                    action.execution_finished_at = datetime.now(timezone.utc)
                    observations.append({"action_id": action.id, "capability": action.capability_name, "status": "failed", "summary": "Runtime execution failed", "error": str(exc)[:1000]})
                    if action.effect == "change":
                        rollback = self.executor.rollback(db, action, definition)
                        observations.append({**rollback, "rollback_for": action.id})
                        action.status = self._rollback_action_status(rollback)
                    append_audit_event(db, actor_type="agent", actor_id=state["run_id"], event_type="action.executed", payload={"status": action.status}, project_id=action.project_id, environment_id=action.environment_id, run_id=action.run_id, action_id=action.id)
                    db.commit()
                    continue
                if not self._execution_owned(db, action.id, execution_token):
                    db.rollback()
                    observations.append({
                        "action_id": action.id,
                        "status": "execution_unknown",
                        "summary": "Late execution result was ignored after the Worker lost its lease",
                    })
                    continue
                action = db.get(Action, action.id)
                executed_calls += 1
                observations.append(observation)
                action.execution_finished_at = datetime.now(timezone.utc)
                if action.effect == "read":
                    action.status = "succeeded" if observation["status"] == "success" else "failed"
                    terminal_error = self._terminal_runtime_error(observation)
                    if terminal_error:
                        append_audit_event(
                            db,
                            actor_type="agent",
                            actor_id=state["run_id"],
                            event_type="action.executed",
                            payload={"status": action.status, "error_code": terminal_error[0]},
                            project_id=action.project_id,
                            environment_id=action.environment_id,
                            run_id=action.run_id,
                            action_id=action.id,
                        )
                        break
                if action.effect == "change" and observation["status"] != "success":
                    action.status = "failed"
                    rollback = self.executor.rollback(db, action, definition)
                    observations.append({**rollback, "rollback_for": action.id})
                    action.status = self._rollback_action_status(rollback)
                if action.effect == "change" and observation["status"] == "success" and definition.verifier:
                    verifier = self._bound_related_definition(action, "verifier")
                    if not verifier:
                        action.status = "verification_failed"
                        observations.append({"action_id": action.id, "status": "failed", "summary": "Registered verifier is missing"})
                        rollback = self.executor.rollback(db, action, definition)
                        observations.append({**rollback, "rollback_for": action.id})
                        action.status = self._rollback_action_status(rollback)
                        append_audit_event(db, actor_type="agent", actor_id=state["run_id"], event_type="action.executed", payload={"status": action.status}, project_id=action.project_id, environment_id=action.environment_id, run_id=action.run_id, action_id=action.id)
                        continue
                    verify_arguments = {name: action.arguments_json[name] for name in verifier.arguments if name in action.arguments_json}
                    verify_resolved = {**action.resolved_spec_json, "capability_bindings": registry.related_bindings(verifier)}
                    verification_token = str(uuid4())
                    verify_action = Action(id=str(uuid4()), run_id=action.run_id, capability_name=verifier.name, capability_version=verifier.version, capability_definition_hash=registry.definition_hash(verifier.name, verifier.version), risk_level=verifier.risk_level, approval_mode=verifier.approval_mode, policy_version=self.policy.policy_version, config_revision=action.config_revision, project_id=action.project_id, environment_id=action.environment_id, target_json=action.target_json, arguments_json=verify_arguments, resolved_spec_json=verify_resolved, rollback_spec_json={}, purpose="Post-change verification", effect="read", action_hash="", status="executing", execution_token=verification_token, execution_started_at=datetime.now(timezone.utc))
                    verify_action.action_hash = compute_action_hash(action_snapshot(verify_action))
                    db.add(verify_action)
                    db.flush()
                    verify_policy = self.policy.evaluate(db, verify_action, verifier, state["user_id"])
                    db.add(PolicyDecision(action_id=verify_action.id, decision=verify_policy.decision, risk_level=verify_policy.risk_level, reason_code=verify_policy.reason_code, reason=verify_policy.reason, matched_policies_json=verify_policy.matched_policies, policy_version=self.policy.policy_version))
                    if verify_policy.decision != "allow":
                        verify_action.status = "denied"
                        verification = {"capability": verifier.name, "status": "denied", "summary": verify_policy.reason}
                    else:
                        db.commit()
                        action = db.get(Action, action.id)
                        verify_action = db.get(Action, verify_action.id)
                        verification = self.executor.execute(db, verify_action, verifier)
                        if not self._execution_owned(db, action.id, execution_token) or not self._execution_owned(db, verify_action.id, verification_token):
                            db.rollback()
                            observations.append({
                                "action_id": action.id,
                                "status": "execution_unknown",
                                "summary": "Late verification result was ignored after the Worker lost its lease",
                            })
                            continue
                        action = db.get(Action, action.id)
                        verify_action = db.get(Action, verify_action.id)
                        executed_calls += 1
                        verify_action.status = "succeeded" if verification["status"] == "success" else "failed"
                        verify_action.execution_finished_at = datetime.now(timezone.utc)
                    verification["verification_for"] = action.id
                    observations.append(verification)
                    action.status = "verified" if self._verification_satisfied(action, verification) else "verification_failed"
                    if action.status == "verification_failed":
                        rollback = self.executor.rollback(db, action, definition)
                        observations.append({**rollback, "rollback_for": action.id})
                        action.status = self._rollback_action_status(rollback)
                    elif action.status == "verified":
                        self.executor.finalize(db, action, definition)
                append_audit_event(db, actor_type="agent", actor_id=state["run_id"], event_type="action.executed", payload={"status": action.status}, project_id=action.project_id, environment_id=action.environment_id, run_id=action.run_id, action_id=action.id)
            self._step(db, state, "execute", {"observations": len(observations)})
            db.commit()
        result = {
            "evidence": observations,
            "tool_call_count": state.get("tool_call_count", 0) + executed_calls,
            "action_ids": [],
            "pending_calls": [],
            "step_count": state.get("step_count", 0) + 1,
            "status": "failed" if terminal_error else "running",
        }
        if terminal_error:
            result["answer"] = terminal_error[1]
        return result

    def finish(self, state: AgentState) -> dict:
        answer = state.get("answer") or "目前没有足够信息形成可靠回答，请补充目标或选择项目环境后重试。"
        with SessionLocal() as db:
            run = db.get(AgentRun, state["run_id"])
            requested_status = state.get("status") if state.get("status") in {"failed", "cancelled"} else "completed"
            now = datetime.now(timezone.utc)
            db.refresh(run)
            if run.status in {"completed", "failed", "cancelled"}:
                terminal_status = run.status
            elif requested_status == "cancelled":
                cancelled = db.scalar(
                    update(AgentRun)
                    .where(AgentRun.id == run.id, AgentRun.status.not_in(["completed", "failed"]))
                    .values(status="cancelled", cancel_requested_at=now, completed_at=now, current_step="finish")
                    .returning(AgentRun.status)
                )
                if cancelled:
                    terminal_status = "cancelled"
                else:
                    db.refresh(run)
                    terminal_status = run.status
            else:
                finished = db.scalar(
                    update(AgentRun)
                    .where(
                        AgentRun.id == run.id,
                        AgentRun.status == "running",
                        AgentRun.cancel_requested_at.is_(None),
                    )
                    .values(status=requested_status, completed_at=now, current_step="finish")
                    .returning(AgentRun.status)
                )
                terminal_status = str(finished or "cancelled")
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
            approved = db.scalar(
                select(Action.id)
                .join(Approval)
                .where(
                    Action.id.in_(state.get("action_ids", [])),
                    Action.status == "approved",
                    Approval.decision == "approved",
                )
                .limit(1)
            )
        return "execute" if approved else "finish"

    @staticmethod
    def route_after_execute(state: AgentState) -> str:
        return "finish" if state.get("status") in {"failed", "cancelled", "completed"} else "decide"

    @staticmethod
    def _terminal_runtime_error(observation: dict) -> tuple[str, str] | None:
        error_code = observation.get("error_code") or (observation.get("data") or {}).get("error_code")
        messages = {
            "ssh_credential_not_configured": "无法连接项目服务器：项目尚未配置 SSH 私钥。请先在连接配置中登记私钥后再试。",
            "ssh_credential_missing": "无法连接项目服务器：运行容器中没有找到 SSH 私钥。请执行 `docker compose up -d --force-recreate backend worker` 恢复密钥挂载后再试。",
            "ssh_credential_unreadable": "无法连接项目服务器：运行容器没有权限读取 SSH 私钥。请修正密钥文件权限后再试。",
            "ssh_authentication_failed": "无法登录项目服务器：SSH 身份验证失败。请检查用户名、私钥和 authorized_keys 配置。",
            "ssh_host_key_mismatch": "已停止连接：SSH 主机指纹与登记值不一致。请先确认目标服务器身份，再更新连接配置。",
            "connection_not_found": "当前环境没有配置运行时连接，暂时无法执行项目检查。请先完成环境连接配置。",
            "environment_not_found": "当前运行环境不存在或已失效，暂时无法执行项目检查。",
            "configuration_revision_mismatch": "运行环境配置已发生变化，本次检查已安全停止。请重新发起请求。",
            "capability_binding_mismatch": "能力定义已发生变化，本次检查已安全停止。请重新发起请求。",
        }
        message = messages.get(str(error_code))
        return (str(error_code), message) if message else None

    @staticmethod
    def _verification_satisfied(action: Action, observation: dict) -> bool:
        return verification_satisfied(action, observation)

    @staticmethod
    def _precheck_satisfied(action: Action, observation: dict) -> bool:
        if observation.get("status") != "success":
            return False
        if action.capability_name in {"service.start", "service.stop", "service.restart", "service.scale"}:
            data = observation.get("data") or {}
            if OpsAgentGraph._runtime_records(data) or "activestate=" in json.dumps(data).lower():
                return True
            return bool(
                action.capability_name == "service.scale"
                and data.get("parse_valid") is True
                and data.get("records") == []
            )
        return True

    @staticmethod
    def _runtime_records(data: dict) -> list[dict]:
        return runtime_records(data)

    def _run_precheck(self, db, state: AgentState, change_action: Action, change_definition) -> dict:
        precheck_name = change_definition.precheck
        if not precheck_name:
            return {"action_id": change_action.id, "capability": "precheck", "status": "failed", "summary": "No registered precheck is available"}
        definition = self._bound_related_definition(change_action, "precheck")
        if not definition or definition.name != precheck_name:
            return {"action_id": change_action.id, "capability": precheck_name, "status": "failed", "summary": "Registered precheck binding is missing or has changed"}
        arguments = {name: change_action.arguments_json[name] for name in definition.arguments if name in change_action.arguments_json}
        precheck_resolved = {**change_action.resolved_spec_json, "capability_bindings": registry.related_bindings(definition)}
        execution_token = str(uuid4())
        precheck_action = Action(id=str(uuid4()), run_id=change_action.run_id, capability_name=definition.name, capability_version=definition.version, capability_definition_hash=registry.definition_hash(definition.name, definition.version), risk_level=definition.risk_level, approval_mode=definition.approval_mode, policy_version=self.policy.policy_version, config_revision=change_action.config_revision, project_id=change_action.project_id, environment_id=change_action.environment_id, target_json=change_action.target_json, arguments_json=arguments, resolved_spec_json=precheck_resolved, rollback_spec_json={}, purpose=f"Precheck for {change_action.capability_name}", effect="read", action_hash="", status="executing", execution_token=execution_token, execution_started_at=datetime.now(timezone.utc))
        precheck_action.action_hash = compute_action_hash(action_snapshot(precheck_action))
        db.add(precheck_action); db.flush()
        policy = self.policy.evaluate(db, precheck_action, definition, state["user_id"])
        db.add(PolicyDecision(action_id=precheck_action.id, decision=policy.decision, risk_level=policy.risk_level, reason_code=policy.reason_code, reason=policy.reason, matched_policies_json=policy.matched_policies, policy_version=self.policy.policy_version))
        if policy.decision != "allow":
            precheck_action.status = "denied"
            return {"action_id": precheck_action.id, "capability": definition.name, "status": "denied", "summary": policy.reason}
        db.commit()
        precheck_action = db.get(Action, precheck_action.id)
        observation = self.executor.execute(db, precheck_action, definition)
        if not self._execution_owned(db, precheck_action.id, execution_token):
            db.rollback()
            return {
                "action_id": precheck_action.id,
                "capability": definition.name,
                "status": "execution_unknown",
                "summary": "Late precheck result was ignored after the Run stopped",
            }
        precheck_action = db.get(Action, precheck_action.id)
        precheck_action.status = "succeeded" if observation.get("status") == "success" else "failed"
        precheck_action.execution_finished_at = datetime.now(timezone.utc)
        return observation

    @staticmethod
    def _action_snapshot(action: Action) -> dict[str, Any]:
        return action_snapshot(action)

    @staticmethod
    def _bound_related_definition(action: Action, relation: str):
        bindings = (action.resolved_spec_json or {}).get("capability_bindings") or {}
        return registry.get_from_binding(bindings.get(relation))

    @staticmethod
    def _action_bindings_available(action: Action) -> bool:
        bindings = (action.resolved_spec_json or {}).get("capability_bindings")
        if not registry.bindings_available(bindings):
            return False
        primary = bindings.get("action")
        if not isinstance(primary, dict) or (
            primary.get("name") != action.capability_name
            or primary.get("version") != action.capability_version
            or primary.get("definition_hash") != action.capability_definition_hash
        ):
            return False
        rollback_spec = action.rollback_spec_json or {}
        if rollback_spec.get("kind") != "capability":
            return True
        rollback_bindings = rollback_spec.get("capability_bindings")
        if not registry.bindings_available(rollback_bindings):
            return False
        rollback_primary = rollback_bindings.get("action")
        return bool(
            isinstance(rollback_primary, dict)
            and rollback_primary.get("name") == rollback_spec.get("capability")
        )

    @staticmethod
    def _configuration_revision_matches(db, action: Action) -> bool:
        environment = db.get(Environment, action.environment_id)
        if not environment:
            return False
        connection = db.get(Connection, environment.connection_id) if environment.connection_id else None
        return action.config_revision == configuration_revision(environment, connection)

    @staticmethod
    def _execution_owned(db, action_id: str, execution_token: str) -> bool:
        return bool(
            db.scalar(
                select(Action.id).join(AgentRun, AgentRun.id == Action.run_id).where(
                    Action.id == action_id,
                    Action.status == "executing",
                    Action.execution_token == execution_token,
                    AgentRun.status == "running",
                    AgentRun.cancel_requested_at.is_(None),
                )
            )
        )

    @staticmethod
    def _rollback_action_status(rollback: dict[str, Any]) -> str:
        status = rollback.get("status")
        if status == "success":
            return "rolled_back"
        if status == "execution_unknown":
            return "execution_unknown"
        return "rollback_failed"

    @staticmethod
    def _build_rollback_spec(action: Action, definition, precheck: dict) -> dict[str, Any]:
        original_running = OpsAgentGraph._service_running(precheck)
        records = OpsAgentGraph._runtime_records(precheck.get("data") or {})
        runtime_type = str((action.resolved_spec_json or {}).get("runtime_type") or "")
        kubernetes_replicas: int | None = None
        if runtime_type == "kubernetes" and len(records) == 1 and isinstance(records[0].get("spec"), dict):
            try:
                kubernetes_replicas = int(records[0]["spec"].get("replicas"))
            except (TypeError, ValueError):
                kubernetes_replicas = None
        if kubernetes_replicas is not None:
            original_running = kubernetes_replicas > 0
        if action.capability_name == "service.start":
            if original_running is None:
                return {"kind": "unavailable", "reason": "original service state could not be determined"}
            if original_running:
                return {"kind": "no_op", "reason": "service was already running"}
            return {"kind": "capability", "capability": "service.stop", "arguments": {"service": action.arguments_json["service"]}}
        if action.capability_name == "service.stop":
            if original_running is None:
                return {"kind": "unavailable", "reason": "original service state could not be determined"}
            if original_running is False:
                return {"kind": "no_op", "reason": "service was already stopped"}
            if runtime_type == "kubernetes":
                if kubernetes_replicas is None or kubernetes_replicas <= 0:
                    return {"kind": "unavailable", "reason": "original Kubernetes replica count could not be determined"}
                return {
                    "kind": "capability",
                    "capability": "service.scale",
                    "arguments": {"service": action.arguments_json["service"], "replicas": kubernetes_replicas},
                }
            return {"kind": "capability", "capability": "service.start", "arguments": {"service": action.arguments_json["service"]}}
        if action.capability_name == "service.restart":
            if original_running is True:
                if runtime_type == "kubernetes":
                    if kubernetes_replicas is None or kubernetes_replicas <= 0:
                        return {"kind": "unavailable", "reason": "original Kubernetes replica count could not be determined"}
                    return {
                        "kind": "capability",
                        "capability": "service.scale",
                        "arguments": {"service": action.arguments_json["service"], "replicas": kubernetes_replicas},
                    }
                capability = "service.start"
            elif original_running is False:
                capability = "service.stop"
            else:
                return {"kind": "unavailable", "reason": "original service state could not be determined"}
            return {"kind": "capability", "capability": capability, "arguments": {"service": action.arguments_json["service"]}}
        if action.capability_name == "service.scale":
            if records and isinstance(records[0].get("spec"), dict):
                try:
                    replicas = int(records[0]["spec"].get("replicas"))
                except (TypeError, ValueError):
                    return {"kind": "unavailable", "reason": "original replica count could not be determined"}
            else:
                replicas = len(records)
            return {"kind": "capability", "capability": "service.scale", "arguments": {"service": action.arguments_json["service"], "replicas": replicas}}
        if action.capability_name == "config.update_registered":
            return {"kind": "config_backup", "backup_path": action.resolved_spec_json.get("backup_path")}
        if action.capability_name == "deployment.apply_registered":
            deployment = action.resolved_spec_json.get("registered_deployment") or {}
            return {"kind": "deployment", "mode": deployment.get("rollback") or "rollout_undo", "service": deployment.get("service")}
        if definition.rollback:
            return {"kind": "capability", "capability": definition.rollback, "arguments": action.arguments_json}
        return {"kind": "unavailable"}

    def _policy_checked_rollback_spec(
        self,
        db,
        user_id: int,
        action: Action,
        rollback_spec: dict[str, Any],
    ) -> dict[str, Any]:
        if rollback_spec.get("kind") != "capability":
            return rollback_spec
        definition = registry.get(str(rollback_spec.get("capability") or ""))
        if not definition:
            return {"kind": "unavailable", "reason": "registered rollback capability is missing"}
        arguments = rollback_spec.get("arguments") or {}
        candidate = Action(
            id=str(uuid4()),
            run_id=action.run_id,
            capability_name=definition.name,
            capability_version=definition.version,
            capability_definition_hash=registry.definition_hash(definition.name, definition.version),
            risk_level=definition.risk_level,
            approval_mode=definition.approval_mode,
            policy_version=self.policy.policy_version,
            config_revision=action.config_revision,
            project_id=action.project_id,
            environment_id=action.environment_id,
            target_json=action.target_json,
            arguments_json=arguments,
            resolved_spec_json=action.resolved_spec_json,
            rollback_spec_json={},
            purpose=f"Policy preview for rollback of {action.id}",
            effect=definition.effect,
            action_hash="",
            status="proposed",
        )
        policy = self.policy.evaluate(db, candidate, definition, user_id)
        if policy.decision not in {"allow", "require_approval"}:
            return {"kind": "unavailable", "reason": policy.reason}
        return {**rollback_spec, "capability_bindings": registry.related_bindings(definition)}

    @staticmethod
    def _service_running(observation: dict) -> bool | None:
        data = observation.get("data") or {}
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":")).lower()
        if "activestate=active" in text:
            return True
        if "activestate=inactive" in text:
            return False
        records = OpsAgentGraph._runtime_records(data)
        if records:
            return any(str(item.get("State") or item.get("state") or "").lower() == "running" for item in records)
        return None

    @staticmethod
    def _step(
        db,
        state: AgentState,
        step_type: str,
        output: dict,
        *,
        status: str = "success",
        error_code: str | None = None,
    ) -> None:
        run = db.get(AgentRun, state["run_id"])
        sequence = (run.step_count if run else 0) + 1
        if run:
            run.step_count = sequence
            run.current_step = step_type
        db.add(
            AgentStep(
                run_id=state["run_id"],
                sequence=sequence,
                step_type=step_type,
                status=status,
                output_summary_json=output,
                error_code=error_code,
                finished_at=datetime.now(timezone.utc),
            )
        )
