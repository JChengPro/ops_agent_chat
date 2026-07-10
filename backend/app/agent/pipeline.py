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

        if intent in {"project_knowledge", "mixed"}:
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
        elif intent == "general":
            answer = self._answer_general(user_message.content)
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
            "现在",
            "当前运行",
            "是否正常",
            "打不开",
            "无法访问",
            "挂了",
            "health",
            "502",
            "磁盘",
            "内存",
            "status",
            "log",
        ]
        project_terms = [
            "当前项目",
            "本项目",
            "这个项目",
            "项目里",
            "项目中",
            "项目的",
            "videohub",
            "health 地址",
            "health地址",
            "数据库密码",
            "服务名",
        ]
        general_terms = [
            "是什么",
            "什么意思",
            "区别",
            "原理",
            "一般",
            "常见",
            "有哪些",
            "为什么会",
            "如何理解",
            "stdout",
            "stderr",
        ]
        mixed_terms = ["结合", "根据文档和日志", "文档和当前", "日志和项目文档", "配置和日志"]
        has_operation_term = any(word in lowered for word in operation_terms)
        has_diagnosis_term = any(word in lowered for word in diagnosis_terms)
        has_project_term = any(word in lowered for word in project_terms)
        has_general_term = any(word in lowered for word in general_terms)
        has_mixed_term = any(word in lowered for word in mixed_terms)
        if has_operation_term:
            return "operation"
        if has_mixed_term or (has_project_term and has_diagnosis_term):
            return "mixed"
        if has_diagnosis_term:
            return "diagnosis"
        if has_project_term and not has_general_term:
            return "project_knowledge"
        if has_general_term and not has_project_term:
            return "general"
        fallback = {"intent_type": "general"}
        prompt = (
            "Classify the user's Ops Agent Chat request. Return JSON only: "
            '{"intent_type":"general|project_knowledge|diagnosis|operation|mixed"}. '
            "general means generic technical knowledge that does not depend on this specific project. "
            "project_knowledge means facts about this project, such as its deployment, ports, services, config, paths, or docs. "
            "Operation means the user asks to change runtime state, such as restart, stop, delete, deploy, modify, or cleanup. "
            "Read-only status checks, log checks, health checks, and Redis/MySQL status questions are diagnosis, not operation."
        )
        result = self.llm.json_completion(prompt, message, fallback)
        intent = result.get("intent_type", fallback["intent_type"])
        if intent == "operation" and not has_operation_term:
            corrected = "diagnosis" if has_diagnosis_term else ("project_knowledge" if has_project_term else "general")
            logger.warning(
                "IntentRouter override: model returned operation without change verb; using %s. message=%r",
                corrected,
                message,
            )
            return corrected
        if intent == "knowledge":
            return "project_knowledge"
        return intent if intent in {"general", "project_knowledge", "diagnosis", "operation", "mixed"} else fallback["intent_type"]

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
        has_project_evidence = self._has_project_evidence(rag_chunks)
        if intent == "project_knowledge" and not has_project_evidence:
            return self._answer_without_project_evidence(question)
        context = {
            "project": {"name": project.name, "deploy_type": project.deploy_type, "health_url": project.health_url},
            "project_evidence_available": has_project_evidence,
            "rag_chunks": [self._source_dict(chunk) | {"content": chunk.content[:1200]} for chunk in rag_chunks if chunk.score > 0.05],
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
            "Use command outputs and provided project RAG chunks when they exist. "
            "If project_evidence_available is false, explicitly say no current project document evidence was found. "
            "Do not invent project-specific ports, passwords, service names, paths, commands, or deployment details. "
            "Separate conclusion, evidence, and next steps. "
            "Use plain section headings exactly as 诊断结论, 证据, 下一步建议. "
            "Never output headings such as 结论, 证据, 后续建议, 下一步建议 with quotes, markdown bold, code fences, or JSON. "
            "Do not claim a command succeeded if status or exit_code says otherwise. Do not suggest executing change commands directly in V1."
        )
        user = json.dumps({"question": question, "context": context}, ensure_ascii=False)
        answer = self.llm.text_completion(system, user, fallback)
        return self._normalize_answer_text(answer)

    def _answer_general(self, question: str) -> str:
        fallback = self._fallback_general_answer(question)
        system = (
            "You are Ops Agent Chat. Answer in Chinese using general DevOps knowledge only. "
            "Do not claim to know this user's current project configuration, ports, passwords, paths, service names, or runtime status. "
            "Use plain section headings exactly as 诊断结论, 证据, 下一步建议. "
            "Keep the answer concise and practical. Do not output markdown bold headings, quotes, code fences, or JSON."
        )
        answer = self.llm.text_completion(system, question, fallback)
        return self._normalize_answer_text(answer)

    def _answer_without_project_evidence(self, question: str) -> str:
        fallback = self._fallback_no_project_evidence_answer(question)
        system = (
            "You are Ops Agent Chat. The project knowledge base did not return reliable evidence for the user's question. "
            "Answer in Chinese. First state that no current project document evidence was found. "
            "Then provide general DevOps guidance if useful. "
            "Do not invent project-specific ports, passwords, service names, paths, commands, or deployment details. "
            "Suggest adding project docs or asking for a read-only runtime diagnosis when appropriate. "
            "Use plain section headings exactly as 诊断结论, 证据, 下一步建议. "
            "Do not output markdown bold headings, quotes, code fences, or JSON."
        )
        answer = self.llm.text_completion(system, question, fallback)
        return self._normalize_answer_text(answer)

    def _has_project_evidence(self, chunks: list[RetrievedChunk]) -> bool:
        return any(chunk.score > 0.05 for chunk in chunks)

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
        if intent == "project_knowledge":
            return self._fallback_no_project_evidence_answer(question)
        if intent == "general":
            return self._fallback_general_answer(question)
        return "我暂时没有足够证据回答这个问题。"

    def _fallback_general_answer(self, question: str) -> str:
        return (
            "诊断结论\n"
            "这是一个通用技术问题，可以基于通用运维知识先做解释；该回答不引用当前项目文档，也不代表当前项目的实际配置。\n\n"
            "证据\n"
            f"用户问题：{question}\n"
            "当前问题没有要求读取项目配置或检查实时运行状态。\n\n"
            "下一步建议\n"
            "如果你想确认当前项目里的真实状态，请明确让我检查服务状态、日志或健康接口；如果你想确认项目配置，请补充或更新项目文档。"
        )

    def _fallback_no_project_evidence_answer(self, question: str) -> str:
        return (
            "诊断结论\n"
            "当前项目知识库没有检索到足够可靠的项目文档依据，因此不能确认这个项目的具体配置或实现细节。\n\n"
            "证据\n"
            f"用户问题：{question}\n"
            "没有可用的高相关项目文档片段支持直接回答。\n\n"
            "下一步建议\n"
            "可以先按通用运维思路检查服务是否运行、端口是否连通、环境变量是否加载、容器网络是否正常、日志是否有错误。以上是通用建议，不代表当前项目一定采用这些配置。你也可以补充项目文档，或让我执行 V1 允许的只读诊断。"
        )

    def _source_dict(self, chunk: RetrievedChunk) -> dict[str, Any]:
        return {
            "chunk_id": chunk.chunk_id,
            "document_id": chunk.document_id,
            "title": chunk.title,
            "file_name": chunk.file_name,
            "score": chunk.score,
        }
