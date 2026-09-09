from datetime import datetime, timezone
import logging
import re
from typing import Any

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.orm import Session

from app.embeddings.service import EmbeddingProvider, configured_embedding_provider
from app.models.experience import ExperienceChunk, ExperienceItem
from app.experience.chunking import chunk_document


logger = logging.getLogger(__name__)


def lexical_terms(query: str, limit: int = 24) -> list[str]:
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", query.lower()):
        if len(token) > 1:
            terms.append(token)
    for sequence in re.findall(r"[\u3400-\u9fff]+", query):
        if 1 < len(sequence) <= 4:
            terms.append(sequence)
        terms.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return list(dict.fromkeys(terms))[:limit]


def reciprocal_rank_fusion(
    rankings: dict[str, list[int]],
    *,
    rank_constant: int = 60,
) -> dict[int, dict[str, Any]]:
    fused: dict[int, dict[str, Any]] = {}
    for channel, identifiers in rankings.items():
        for rank, identifier in enumerate(identifiers, start=1):
            entry = fused.setdefault(identifier, {"score": 0.0, "ranks": {}})
            entry["score"] += 1.0 / (rank_constant + rank)
            entry["ranks"][channel] = rank
    return fused


def index_experience(
    db: Session,
    item: ExperienceItem,
    *,
    embedder: EmbeddingProvider | None = None,
) -> dict[str, int]:
    source_ref = item.source_ref or f"experience:{item.id}"
    drafts = chunk_document(item.content, source_ref=source_ref)
    existing = {
        chunk.chunk_key: chunk
        for chunk in db.scalars(
            select(ExperienceChunk).where(ExperienceChunk.experience_item_id == item.id)
        )
    }
    active_keys: set[str] = set()
    created = updated = unchanged = 0
    embedding_targets: list[tuple[ExperienceChunk, str]] = []
    for draft in drafts:
        active_keys.add(draft.chunk_key)
        search_text = f"{item.title} {' '.join(item.tags or [])} {' '.join(draft.heading_path)} {draft.content}".lower()
        metadata = {
            "trust_status": item.trust_status,
            "source_type": item.source_type,
            "source_ref": source_ref,
            "heading_path": list(draft.heading_path),
        }
        chunk = existing.get(draft.chunk_key)
        if chunk is None:
            chunk = ExperienceChunk(
                    experience_item_id=item.id,
                    project_id=item.project_id,
                    chunk_key=draft.chunk_key,
                    content_hash=draft.content_hash,
                    source_ref=source_ref,
                    heading_path=list(draft.heading_path),
                    chunk_index=draft.chunk_index,
                    content=draft.content,
                    search_text=search_text,
                    metadata_json=metadata,
                )
            db.add(chunk)
            embedding_targets.append((chunk, draft.content))
            created += 1
            continue
        changed = chunk.content_hash != draft.content_hash
        chunk.project_id = item.project_id
        chunk.content_hash = draft.content_hash
        chunk.source_ref = source_ref
        chunk.heading_path = list(draft.heading_path)
        chunk.chunk_index = draft.chunk_index
        chunk.content = draft.content
        chunk.search_text = search_text
        chunk.metadata_json = metadata
        if changed:
            chunk.embedding_json = None
            chunk.embedding = None
            embedding_targets.append((chunk, draft.content))
            updated += 1
        else:
            unchanged += 1
    stale_keys = set(existing) - active_keys
    if stale_keys:
        db.execute(
            delete(ExperienceChunk).where(
                ExperienceChunk.experience_item_id == item.id,
                ExperienceChunk.chunk_key.in_(stale_keys),
            )
        )
    embedding_failed = 0
    if embedding_targets:
        try:
            provider = embedder or configured_embedding_provider()
            if provider:
                vectors = provider.embed([content for _, content in embedding_targets])
                if len(vectors) != len(embedding_targets):
                    raise RuntimeError("Embedding provider returned the wrong number of vectors")
                for (chunk, _), vector in zip(embedding_targets, vectors, strict=True):
                    chunk.embedding = vector
        except Exception as exc:  # noqa: BLE001
            embedding_failed = len(embedding_targets)
            logger.warning("Experience embedding generation failed; lexical index remains available: %s", type(exc).__name__)
    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "deleted": len(stale_keys),
        "embedding_failed": embedding_failed,
    }


def search_experience(
    db: Session,
    project_id: int,
    query: str,
    limit: int = 5,
    *,
    embedder: EmbeddingProvider | None = None,
) -> dict:
    words = lexical_terms(query)
    candidate_limit = max(limit * 4, 20)
    base_statement = (
        select(ExperienceChunk, ExperienceItem)
        .join(ExperienceItem, ExperienceItem.id == ExperienceChunk.experience_item_id)
        .where(ExperienceChunk.project_id == project_id, ExperienceItem.trust_status == "verified")
    )
    lexical_rows: list[tuple[ExperienceChunk, ExperienceItem]] = []
    if words:
        search_query = func.plainto_tsquery("simple", " ".join(words))
        search_vector = func.to_tsvector("simple", ExperienceChunk.search_text)
        like_conditions = [ExperienceChunk.search_text.ilike(f"%{word}%") for word in words]
        lexical_score = func.ts_rank_cd(search_vector, search_query) + sum(
            (case((condition, 1.0), else_=0.0) for condition in like_conditions),
            start=0.0,
        )
        lexical_rows = db.execute(
            base_statement.where(or_(search_vector.op("@@")(search_query), *like_conditions))
            .order_by(lexical_score.desc(), ExperienceChunk.id)
            .limit(candidate_limit)
        ).all()

    vector_rows: list[tuple[ExperienceChunk, ExperienceItem]] = []
    embedding_error = None
    try:
        provider = embedder or configured_embedding_provider()
        if provider:
            vectors = provider.embed([query])
            if len(vectors) != 1:
                raise RuntimeError("Embedding provider returned the wrong number of query vectors")
            distance = ExperienceChunk.embedding.cosine_distance(vectors[0])
            vector_rows = db.execute(
                base_statement.where(ExperienceChunk.embedding.is_not(None))
                .order_by(distance, ExperienceChunk.id)
                .limit(candidate_limit)
            ).all()
    except Exception as exc:  # noqa: BLE001
        embedding_error = type(exc).__name__
        logger.warning("Experience vector search failed; using lexical results: %s", embedding_error)

    rows_by_id: dict[int, tuple[ExperienceChunk, ExperienceItem]] = {}
    for chunk, item in [*lexical_rows, *vector_rows]:
        rows_by_id[chunk.id] = (chunk, item)
    fused = reciprocal_rank_fusion(
        {
            "lexical": [chunk.id for chunk, _ in lexical_rows],
            "vector": [chunk.id for chunk, _ in vector_rows],
        }
    )
    ranked_ids = sorted(fused, key=lambda identifier: (-fused[identifier]["score"], identifier))[:limit]
    return {
        "query": query,
        "items": [
            {
                "item_id": rows_by_id[identifier][1].id,
                "title": rows_by_id[identifier][1].title,
                "content": rows_by_id[identifier][0].content,
                "chunk_key": rows_by_id[identifier][0].chunk_key,
                "source_ref": rows_by_id[identifier][0].source_ref,
                "heading_path": rows_by_id[identifier][0].heading_path,
                "trust_status": rows_by_id[identifier][1].trust_status,
                "source_type": rows_by_id[identifier][1].source_type,
                "score": fused[identifier]["score"],
                "ranks": fused[identifier]["ranks"],
            }
            for identifier in ranked_ids
        ],
        "retrieval_method": "hybrid_rrf" if vector_rows else "lexical",
        "embedding_error": embedding_error,
        "searched_at": datetime.now(timezone.utc).isoformat(),
    }
