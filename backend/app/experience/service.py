from datetime import datetime, timezone
import re

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.models.experience import ExperienceChunk, ExperienceItem


def index_experience(db: Session, item: ExperienceItem) -> None:
    db.execute(delete(ExperienceChunk).where(ExperienceChunk.experience_item_id == item.id))
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", item.content) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip()
        if current and len(candidate) > 1800:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    for content in chunks or [item.content]:
        db.add(
            ExperienceChunk(
                experience_item_id=item.id,
                project_id=item.project_id,
                content=content,
                search_text=f"{item.title} {' '.join(item.tags or [])} {content}".lower(),
                metadata_json={"trust_status": item.trust_status, "source_type": item.source_type},
            )
        )


def search_experience(db: Session, project_id: int, query: str, limit: int = 5) -> dict:
    words = [word.lower() for word in re.findall(r"[\w\-\.]+", query) if len(word) > 1][:10]
    statement = (
        select(ExperienceChunk, ExperienceItem)
        .join(ExperienceItem, ExperienceItem.id == ExperienceChunk.experience_item_id)
        .where(ExperienceChunk.project_id == project_id, ExperienceItem.trust_status == "verified")
    )
    if words:
        search_query = func.plainto_tsquery("simple", " ".join(words))
        search_vector = func.to_tsvector("simple", ExperienceChunk.search_text)
        statement = statement.where(or_(search_vector.op("@@")(search_query), *(ExperienceChunk.search_text.ilike(f"%{word}%") for word in words)))
    rows = db.execute(statement.limit(limit * 3)).all()
    scored = []
    for chunk, item in rows:
        haystack = chunk.search_text.lower()
        score = sum(haystack.count(word) for word in words) if words else 1
        scored.append((score, chunk, item))
    scored.sort(key=lambda row: row[0], reverse=True)
    return {
        "query": query,
        "items": [
            {
                "item_id": item.id,
                "title": item.title,
                "content": chunk.content,
                "trust_status": item.trust_status,
                "source_type": item.source_type,
                "score": score,
            }
            for score, chunk, item in scored[:limit]
        ],
        "searched_at": datetime.now(timezone.utc).isoformat(),
    }
