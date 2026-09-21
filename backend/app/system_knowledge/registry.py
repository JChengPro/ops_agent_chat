from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.experience.service import lexical_terms


DOCUMENT_METADATA = {
    "system-guide": ("系统功能与账号使用", "能力边界、Skill、账号登录、任务状态以及证据和引用的使用说明。"),
    "project-guide": ("项目接入与文档管理", "项目与环境配置、多项目隔离、上下文采集、文档上传与检索排查。"),
    "deployment-guide": ("部署、升级与维护", "服务启动、数据库迁移、网络访问、备份恢复和故障定位。"),
    "ssh-errors": ("SSH 连接与安全", "SSH 密钥、身份验证、主机指纹、网络连接与命令超时处理。"),
    "runtime-errors": ("Docker 与运行时", "容器退出、健康检查、Compose 服务、HTTP 健康端点与执行验证。"),
    "approval-execution": ("审批与安全执行", "Action 审批、Hash 失效、幂等消费、Worker 租约与未知执行结果。"),
    "monitoring": ("主动巡检与自动修复", "巡检开关、连接异常、自动修复条件、验证失败与 Worker 可用性。"),
    "model-rag": ("模型与 RAG", "模型配置、结构化输出、Embedding、检索降级、向量维度和延迟取舍。"),
}


@dataclass(frozen=True)
class SystemKnowledgeItem:
    id: str
    title: str
    summary: str
    content: str
    tags: tuple[str, ...]
    document_id: str
    document_title: str

    def public_dict(self, *, include_content: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "tags": list(self.tags),
            "document_id": self.document_id,
            "document_title": self.document_title,
            "read_only": True,
        }
        if include_content:
            payload["content"] = self.content
        return payload


class SystemKnowledgeRegistry:
    def __init__(self, definitions_path: Path | None = None) -> None:
        root = definitions_path or Path(__file__).parent / "definitions"
        self._items: dict[str, SystemKnowledgeItem] = {}
        self._documents: dict[str, dict[str, Any]] = {}
        for path in sorted(root.glob("*.yml")):
            document_id = path.stem
            document_title, document_summary = DOCUMENT_METADATA.get(
                document_id,
                (document_id.replace("-", " ").title(), "系统内置运维知识。"),
            )
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or []
            document_items: list[SystemKnowledgeItem] = []
            for raw in payload:
                item = SystemKnowledgeItem(
                    id=str(raw.get("id") or "").strip(),
                    title=str(raw.get("title") or "").strip(),
                    summary=str(raw.get("summary") or "").strip(),
                    content=str(raw.get("content") or "").strip(),
                    tags=tuple(str(tag).strip() for tag in raw.get("tags") or () if str(tag).strip()),
                    document_id=document_id,
                    document_title=document_title,
                )
                if not item.id or not item.title or not item.summary or not item.content:
                    raise ValueError(f"System knowledge entry in {path} is incomplete")
                if item.id in self._items:
                    raise ValueError(f"Duplicate system knowledge id: {item.id}")
                self._items[item.id] = item
                document_items.append(item)
            self._documents[document_id] = {
                "id": document_id,
                "title": document_title,
                "summary": document_summary,
                "items": document_items,
            }

    def list(self) -> list[dict[str, Any]]:
        return [item.public_dict() for item in self._items.values()]

    def documents(self) -> list[dict[str, Any]]:
        return [
            {
                "id": document["id"],
                "title": document["title"],
                "summary": document["summary"],
                "read_only": True,
                # Older clients render content directly instead of iterating items.
                "content": "\n\n".join(
                    f"## {item.title}\n\n{item.summary}\n\n{item.content}"
                    for item in document["items"]
                ),
                "items": [item.public_dict() for item in document["items"]],
            }
            for document in self._documents.values()
        ]

    def get(self, item_id: str) -> SystemKnowledgeItem | None:
        return self._items.get(item_id)

    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        terms = lexical_terms(query)
        scored: list[tuple[int, str, SystemKnowledgeItem]] = []
        for item in self._items.values():
            text = f"{item.id} {item.title} {item.summary} {' '.join(item.tags)} {item.content}".lower()
            score = sum(text.count(term) for term in terms)
            if score:
                scored.append((score, item.id, item))
        scored.sort(key=lambda row: (-row[0], row[1]))
        return {
            "query": query,
            "items": [
                {**item.public_dict(), "score": score}
                for score, _, item in scored[:limit]
            ],
        }


system_knowledge_registry = SystemKnowledgeRegistry()
