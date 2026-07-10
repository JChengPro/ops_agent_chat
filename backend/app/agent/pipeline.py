from dataclasses import dataclass
import json
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.deepseek_client import DeepSeekClient
from app.models.command import CommandPlan, CommandRun
from app.models.chat import ChatMessage, ChatSession
from app.models.project import Project
from app.models.server import Server
from app.rag.retriever import RetrievedChunk, search_project_chunks
from app.ruleguard.checker import RuleGuard
from app.ssh.executor import SSHExecutor

logger = logging.getLogger(__name__)


@dataclass
class AgentResponse:
    assistant_message: ChatMessage
    command_runs: list[CommandRun]
    command_plan: dict[str, Any] | None
    rag_sources: list[dict[str, Any]]


class AgentPipeline:
    def __init__(self) -> None:
        self.llm = DeepSeekClient()
        self.ruleguard = RuleGuard()
        self.ssh = SSHExecutor()

    def handle_user_message(self, db: Session, session: ChatSession, user_message: ChatMessage) -> AgentResponse:
        project = db.get(Project, session.project_id)
        if not project:
            raise ValueError("Project not found")
        server = db.get(Server, project.server_id)
        if not server:
            raise ValueError("Server not found")

        intent = self._classify(user_message.content)
        rag_chunks: list[RetrievedChunk] = []
        command_plan: dict[str, Any] | None = None
        command_runs: list[CommandRun] = []

        if intent in {"knowledge", "mixed"}:
            rag_chunks = search_project_chunks(db, project.id, user_message.content, limit=5)

        if intent in {"diagnosis", "mixed"}:
            command_plan = self._generate_command_plan(user_message.content, project)
            plan_row = CommandPlan(
                session_id=session.id,
                project_id=project.id,
                user_message_id=user_message.id,
                plan_json=command_plan,
                status="generated",
            )
            db.add(plan_row)
            db.flush()
            command_runs = self._execute_l0_plan(db, session, project, server, plan_row, command_plan)

        if intent == "operation":
            answer = "V1 只支持只读诊断，不执行重启、停止、删除或修改类操作。你可以先让我检查服务状态、日志和健康接口。"
        else:
            answer = self._analyze_and_answer(user_message.content, project, intent, rag_chunks, command_runs)
            for run in command_runs:
                run.analysis_summary = answer[:2000]

        assistant = ChatMessage(
            session_id=session.id,
            project_id=project.id,
            role="assistant",
            content=answer,
            message_type="text",
            metadata_json={
                "intent": intent,
                "command_plan_id": command_runs[0].command_plan_id if command_runs else None,
                "command_run_ids": [run.id for run in command_runs],
                "rag_sources": [self._source_dict(chunk) for chunk in rag_chunks],
            },
        )
        db.add(assistant)
        db.flush()
        return AgentResponse(assistant, command_runs, command_plan, [self._source_dict(chunk) for chunk in rag_chunks])

    def _classify(self, message: str) -> str:
        lowered = message.lower()
        operation_terms = [
            "重启",
            "停止",
            "删除",
            "清理",
            "修改",
            "启动服务",
            "关闭",
            "restart",
            "stop",
            "delete",
            "remove",
            "down",
            "up -d",
            "rm ",
            "chmod",
            "chown",
        ]
        diagnosis_terms = [
            "检查",
            "查看",
            "状态",
            "日志",
            "打不开",
            "无法访问",
            "挂了",
            "health",
            "502",
            "redis",
            "mysql",
            "rabbitmq",
            "磁盘",
            "内存",
            "端口",
            "status",
            "log",
        ]
        has_operation_term = any(word in lowered for word in operation_terms)
        has_diagnosis_term = any(word in lowered for word in diagnosis_terms)
        if has_operation_term:
            return "operation"
        if has_diagnosis_term:
            return "diagnosis"
        fallback = {"intent_type": "knowledge"}
        prompt = (
            "Classify the user's Ops Agent Chat request. Return JSON only: "
            '{"intent_type":"knowledge|diagnosis|operation|mixed"}. '
            "Operation means the user asks to change runtime state, such as restart, stop, delete, deploy, modify, or cleanup. "
            "Read-only status checks, log checks, health checks, and Redis/MySQL status questions are diagnosis, not operation."
        )
        result = self.llm.json_completion(prompt, message, fallback)
        intent = result.get("intent_type", fallback["intent_type"])
        if intent == "operation" and not has_operation_term:
            corrected = "diagnosis" if has_diagnosis_term else "knowledge"
            logger.warning(
                "IntentRouter override: model returned operation without change verb; using %s. message=%r",
                corrected,
                message,
            )
            return corrected
        return intent if intent in {"knowledge", "diagnosis", "operation", "mixed"} else fallback["intent_type"]

    def _generate_command_plan(self, message: str, project: Project) -> dict[str, Any]:
        compose = project.compose_file or "docker-compose.yml"
        known_services = project.known_services or []
        prefix = (project.allowed_container_prefixes or [""])[0]
        ps_command = "docker ps"
        if prefix:
            ps_command = f"docker ps --filter name={prefix}"
        fallback_commands = [
            {
                "command": ps_command,
                "purpose": "查看当前项目相关容器是否运行",
                "expected_risk_hint": "read_only",
                "timeout_seconds": 15,
            },
            {
                "command": f"docker compose -f {compose} ps",
                "purpose": "查看 Docker Compose 服务状态",
                "expected_risk_hint": "read_only",
                "timeout_seconds": 20,
            },
        ]
        if project.health_url:
            fallback_commands.append(
                {
                    "command": f"curl -s -i {project.health_url}",
                    "purpose": "检查项目健康接口",
                    "expected_risk_hint": "read_only",
                    "timeout_seconds": 10,
                }
            )
        for service in self._preferred_log_services(message, known_services):
            fallback_commands.append(
                {
                    "command": f"docker logs --tail 200 {service}",
                    "purpose": f"查看 {service} 最近日志",
                    "expected_risk_hint": "read_only",
                    "timeout_seconds": 20,
                }
            )
        system = (
            "You are CommandAgent for Ops Agent Chat V1. Generate read-only diagnosis commands only. "
            "Return JSON with keys goal and commands. Each command has command, purpose, "
            "expected_risk_hint, timeout_seconds. Never include cwd. Never generate restart, down, up, rm, mv, chmod, chown, sudo, bash, sh, pipes, redirection, &&, or ;."
        )
        user = json.dumps(
            {
                "user_message": message,
                "project": {
                    "name": project.name,
                    "deploy_type": project.deploy_type,
                    "compose_file": project.compose_file,
                    "health_url": project.health_url,
                    "known_services": project.known_services,
                    "allowed_container_prefixes": project.allowed_container_prefixes,
                },
            },
            ensure_ascii=False,
        )
        fallback = {"goal": "Read-only diagnosis", "commands": fallback_commands}
        result = self.llm.json_completion(system, user, fallback)
        if not isinstance(result.get("commands"), list):
            return fallback
        return {"goal": result.get("goal", "Read-only diagnosis"), "commands": result["commands"][:5]}

    def _preferred_log_services(self, message: str, known_services: list[str]) -> list[str]:
        lowered = message.lower()
        groups = [
            ("redis", ["redis"]),
            ("mysql", ["mysql", "database", "db", "数据库"]),
            ("rabbitmq", ["rabbitmq", "mq", "queue", "队列"]),
            ("worker", ["worker", "任务"]),
            ("backend", ["backend", "api", "后端", "服务"]),
        ]
        selected: list[str] = []
        for service in known_services:
            service_lower = service.lower()
            if any(group in service_lower and any(keyword in lowered for keyword in keywords) for group, keywords in groups):
                selected.append(service)
        if selected:
            return selected[:2]
        return [service for service in known_services if re.search(r"(backend|api|worker)", service)][:2]

    def _execute_l0_plan(
        self,
        db: Session,
        session: ChatSession,
        project: Project,
        server: Server,
        plan_row: CommandPlan,
        plan: dict[str, Any],
    ) -> list[CommandRun]:
        runs: list[CommandRun] = []
        for item in plan.get("commands", []):
            command = str(item.get("command", "")).strip()
            purpose = str(item.get("purpose", "")).strip()
            timeout = int(item.get("timeout_seconds", 20) or 20)
            decision = self.ruleguard.check(command, project)
            run = CommandRun(
                command_plan_id=plan_row.id,
                session_id=session.id,
                project_id=project.id,
                server_id=server.id,
                command=command,
                cwd=project.workdir,
                purpose=purpose,
                risk_level=decision.risk_level,
                status="pending",
                ruleguard_result=decision.to_dict(),
            )
            db.add(run)
            db.flush()
            if not decision.allowed:
                run.status = "rejected"
                run.stderr_excerpt = decision.reason
                runs.append(run)
                continue
            result = self.ssh.execute(server, project, command, timeout_seconds=min(max(timeout, 5), 30))
            run.status = result.status
            run.exit_code = result.exit_code
            run.stdout_excerpt = result.stdout
            run.stderr_excerpt = result.stderr
            run.stdout_truncated = result.stdout_truncated
            run.stderr_truncated = result.stderr_truncated
            run.duration_ms = result.duration_ms
            run.started_at = result.started_at
            run.finished_at = result.finished_at
            runs.append(run)
        db.flush()
        return runs

    def _analyze_and_answer(
        self,
        question: str,
        project: Project,
        intent: str,
        rag_chunks: list[RetrievedChunk],
        command_runs: list[CommandRun],
    ) -> str:
        if intent == "knowledge" and not rag_chunks:
            return "当前项目知识库没有检索到足够相关的文档。你可以补充部署、排障或接口说明文档后再问。"
        context = {
            "project": {"name": project.name, "deploy_type": project.deploy_type, "health_url": project.health_url},
            "rag_chunks": [self._source_dict(chunk) | {"content": chunk.content[:1200]} for chunk in rag_chunks],
            "command_results": [
                {
                    "command": run.command,
                    "purpose": run.purpose,
                    "status": run.status,
                    "exit_code": run.exit_code,
                    "stdout": (run.stdout_excerpt or "")[:2000],
                    "stderr": (run.stderr_excerpt or "")[:1000],
                    "ruleguard": run.ruleguard_result,
                }
                for run in command_runs
            ],
        }
        fallback = self._fallback_answer(question, intent, rag_chunks, command_runs)
        system = (
            "You are ResultAnalyzer for Ops Agent Chat V1. Answer in Chinese. "
            "Use only provided RAG chunks and command outputs. Separate conclusion, evidence, and next steps. "
            "Use plain section headings exactly as 诊断结论, 证据, 下一步建议. "
            "Never output headings such as 结论, 证据, 后续建议, 下一步建议 with quotes, markdown bold, code fences, or JSON. "
            "Do not claim a command succeeded if status or exit_code says otherwise. Do not suggest executing change commands directly in V1."
        )
        user = json.dumps({"question": question, "context": context}, ensure_ascii=False)
        answer = self.llm.text_completion(system, user, fallback)
        return self._normalize_answer_text(answer)

    def _normalize_answer_text(self, answer: str) -> str:
        title_map = {
            "诊断结论": "诊断结论",
            "结论": "诊断结论",
            "结果": "诊断结论",
            "诊断结果": "诊断结论",
            "证据": "证据",
            "依据": "证据",
            "执行命令": "执行命令",
            "命令": "执行命令",
            "下一步建议": "下一步建议",
            "后续建议": "下一步建议",
            "建议": "下一步建议",
            "下一步": "下一步建议",
            "后续处理": "下一步建议",
            "处理建议": "下一步建议",
            "引用来源": "引用来源",
            "来源": "引用来源",
            "风险提示": "风险提示",
        }
        normalized_lines: list[str] = []
        for line in answer.replace("\r", "").split("\n"):
            title = self._normalize_answer_heading(line)
            normalized_lines.append(title_map.get(title, line))
        return "\n".join(normalized_lines).strip()

    def _normalize_answer_heading(self, line: str) -> str:
        value = line.strip()
        for _ in range(3):
            value = re.sub(r"^#{1,6}\s*", "", value)
            value = re.sub(r"[:：]\s*$", "", value)
            value = re.sub(r"^\*\*(.*)\*\*$", r"\1", value)
            value = re.sub(r"^__([^_].*[^_])__$", r"\1", value)
            value = re.sub(r"^[\"'“”‘’`]+", "", value)
            value = re.sub(r"[\"'“”‘’`]+$", "", value)
            value = value.strip()
        return value

    def _fallback_answer(self, question: str, intent: str, chunks: list[RetrievedChunk], runs: list[CommandRun]) -> str:
        if runs:
            lines = ["我完成了 V1 只读诊断，结果如下："]
            for run in runs:
                lines.append(f"- `{run.command}`：{run.status}，exit_code={run.exit_code}。{run.stderr_excerpt or run.stdout_excerpt or ''}".strip())
            lines.append("建议根据失败命令的 stderr、服务日志和健康检查结果继续定位。V1 不会执行重启或停止操作。")
            return "\n".join(lines)
        if chunks:
            sources = "、".join({chunk.file_name for chunk in chunks})
            lines = [f"根据当前项目知识库（{sources}），我检索到以下相关内容："]
            for chunk in chunks[:3]:
                excerpt = " ".join(chunk.content.split())[:360]
                lines.append(f"- 来源 `{chunk.file_name}`：{excerpt}")
            lines.append("如果你要我检查当前运行状态，请问“为什么项目打不开？”或“帮我看后端日志”，V1 会执行只读诊断命令。")
            return "\n".join(lines)
        return "我暂时没有足够证据回答这个问题。"

    def _source_dict(self, chunk: RetrievedChunk) -> dict[str, Any]:
        return {
            "chunk_id": chunk.chunk_id,
            "document_id": chunk.document_id,
            "title": chunk.title,
            "file_name": chunk.file_name,
            "score": chunk.score,
        }
