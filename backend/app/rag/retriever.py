import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.rag import RagChunk, RagDocument


@dataclass
class RetrievedChunk:
    chunk_id: int
    document_id: int
    title: str
    file_name: str
    content: str
    score: float


OPS_KEYWORDS = {
    "部署",
    "启动",
    "配置",
    "健康",
    "检查",
    "日志",
    "打不开",
    "无法访问",
    "502",
    "nginx",
    "redis",
    "mysql",
    "rabbitmq",
    "docker",
    "compose",
    "容器",
    "端口",
    "磁盘",
    "内存",
    "worker",
    "backend",
    "api",
    "health",
    "troubleshooting",
    "deployment",
}


def _terms(query: str) -> set[str]:
    lowered = query.lower()
    terms = {term.lower() for term in re.findall(r"[a-zA-Z0-9_\-]+", lowered) if len(term) > 1}
    terms.update(keyword for keyword in OPS_KEYWORDS if keyword in lowered)
    # Chinese text is often written without spaces. Add short windows so broad
    # questions like "项目怎么部署" can still match bootstrap docs.
    chinese_parts = re.findall(r"[\u4e00-\u9fff]{2,}", query)
    for part in chinese_parts:
        if len(part) <= 6:
            terms.add(part)
            continue
        for size in (2, 3, 4):
            for index in range(0, len(part) - size + 1):
                terms.add(part[index : index + size])
    return terms


def search_project_chunks(db: Session, project_id: int, query: str, limit: int = 5) -> list[RetrievedChunk]:
    terms = _terms(query)
    rows = db.execute(
        select(RagChunk, RagDocument)
        .join(RagDocument, RagDocument.id == RagChunk.document_id)
        .where(RagChunk.project_id == project_id)
    ).all()
    scored: list[RetrievedChunk] = []
    for chunk, doc in rows:
        content_lower = chunk.content.lower()
        score = sum(content_lower.count(term) for term in terms)
        if score == 0 and terms:
            continue
        scored.append(
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=doc.id,
                title=doc.title,
                file_name=doc.file_name,
                content=chunk.content,
                score=float(score or 0.1),
            )
        )
    scored.sort(key=lambda item: item.score, reverse=True)
    if scored:
        return scored[:limit]
    # V1 fallback: return project-local bootstrap docs instead of answering as
    # if no knowledge base exists. This stays project-scoped and avoids
    # cross-project leakage.
    fallback = [
        RetrievedChunk(
            chunk_id=chunk.id,
            document_id=doc.id,
            title=doc.title,
            file_name=doc.file_name,
            content=chunk.content,
            score=0.01,
        )
        for chunk, doc in rows[:limit]
    ]
    return fallback
