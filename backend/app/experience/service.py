from datetime import datetime, timezone
import hashlib
import json
import logging
import re
import time
from typing import Any

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.embeddings.service import EmbeddingProvider, configured_embedding_provider
from app.models.experience import ExperienceChunk, ExperienceItem
from app.models.agent import ModelCall
from app.experience.chunking import chunk_document
from app.reranking.service import RerankDocument, Reranker, configured_rerank_cache, configured_reranker


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


def lexical_relevance_score(query: str, *, title: str, heading_path: list[str], content: str) -> float:
    terms = lexical_terms(query)
    if not terms:
        return 0.0
    title_text = title.casefold()
    heading_text = " ".join(heading_path).casefold()
    content_text = content.casefold()
    matched = {
        term
        for term in terms
        if term in title_text or term in heading_text or term in content_text
    }
    coverage = len(matched) / len(terms)
    field_score = sum(
        int(term in title_text)
        + 2 * int(term in heading_text)
        + min(content_text.count(term), 3)
        for term in terms
    )
    return 2 * coverage + field_score


def select_diverse_chunks(
    ranked_ids: list[int],
    rows_by_id: dict[int, tuple[ExperienceChunk, ExperienceItem]],
    *,
    limit: int,
    max_chunks_per_item: int,
    context_max_chars: int,
) -> list[int]:
    selected: list[int] = []
    item_counts: dict[int, int] = {}
    used_chars = 0
    for identifier in ranked_ids:
        chunk, item = rows_by_id[identifier]
        if item_counts.get(item.id, 0) >= max_chunks_per_item:
            continue
        if selected and used_chars + len(chunk.content) > context_max_chars:
            continue
        selected.append(identifier)
        item_counts[item.id] = item_counts.get(item.id, 0) + 1
        used_chars += len(chunk.content)
        if len(selected) >= limit:
            break
    return selected


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
            if chunk.embedding is None:
                embedding_targets.append((chunk, draft.content))
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
    environment_id: int | None = None,
    run_id: str | None = None,
    reranker: Reranker | None = None,
) -> dict:
    words = lexical_terms(query)
    settings = get_settings()
    candidate_limit = max(limit * 4, settings.rerank_candidate_limit)
    base_statement = (
        select(ExperienceChunk, ExperienceItem)
        .join(ExperienceItem, ExperienceItem.id == ExperienceChunk.experience_item_id)
        .where(ExperienceChunk.project_id == project_id, ExperienceItem.trust_status == "verified")
    )
    if environment_id is None:
        base_statement = base_statement.where(ExperienceItem.environment_id.is_(None))
    else:
        base_statement = base_statement.where(
            or_(ExperienceItem.environment_id.is_(None), ExperienceItem.environment_id == environment_id)
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
        lexical_rows.sort(
            key=lambda row: (
                -lexical_relevance_score(
                    query,
                    title=row[1].title,
                    heading_path=row[0].heading_path or [],
                    content=row[0].content,
                ),
                row[0].id,
            )
        )

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
    rrf_ranked_ids = sorted(fused, key=lambda identifier: (-fused[identifier]["score"], identifier))
    rerank_candidates = rrf_ranked_ids[: settings.rerank_candidate_limit]
    rerank_source_count = len({rows_by_id[item][1].id for item in rerank_candidates})
    rerank_scores: dict[int, float] = {}
    rerank_error = None
    rerank_applied = False
    rerank_cache_hit = False
    rerank_skipped_reason = None
    active_reranker = reranker
    if rerank_source_count <= limit:
        rerank_skipped_reason = "source_count_not_above_result_limit"
    else:
        if active_reranker is None and run_id:
            try:
                active_reranker = configured_reranker(db, run_id)
            except Exception as exc:  # noqa: BLE001
                rerank_error = type(exc).__name__
        if active_reranker is None:
            rerank_skipped_reason = "reranker_disabled_or_unavailable"
    if active_reranker and rerank_source_count > limit:
        started = time.monotonic()
        document_map = {f"doc_{index}": identifier for index, identifier in enumerate(rerank_candidates)}
        documents = [
            RerankDocument(
                id=document_id,
                text=(
                    f"Title: {rows_by_id[identifier][1].title}\n"
                    f"Section: {' / '.join(rows_by_id[identifier][0].heading_path or [])}\n"
                    f"Content:\n{rows_by_id[identifier][0].content}"
                ),
            )
            for document_id, identifier in document_map.items()
        ]
        request_hash = hashlib.sha256(
            json.dumps(
                {"query": query, "chunk_keys": [rows_by_id[item][0].chunk_key for item in rerank_candidates]},
                ensure_ascii=True,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        status = "success"
        response_json: dict[str, Any] = {}
        try:
            cache = configured_rerank_cache()
            cache_key = hashlib.sha256(
                json.dumps(
                    {
                        "project_id": project_id,
                        "environment_id": environment_id,
                        "provider": active_reranker.provider_name,
                        "model": active_reranker.model,
                        "query": " ".join(query.casefold().split()),
                        "chunks": [
                            [rows_by_id[item][0].chunk_key, rows_by_id[item][0].content_hash]
                            for item in rerank_candidates
                        ],
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            ranked = cache.get(cache_key)
            if ranked is None:
                ranked = active_reranker.rerank(query, documents)
                cache.set(cache_key, ranked)
            else:
                rerank_cache_hit = True
                status = "cached"
            rerank_scores = {document_map[item.id]: item.score for item in ranked}
            rerank_candidates = sorted(
                rerank_candidates,
                key=lambda identifier: (-rerank_scores[identifier], rrf_ranked_ids.index(identifier)),
            )
            rerank_applied = True
            response_json = {
                "ranked_chunk_keys": [rows_by_id[item][0].chunk_key for item in rerank_candidates],
                "scores": [rerank_scores[item] for item in rerank_candidates],
            }
        except Exception as exc:  # noqa: BLE001
            status = "failed"
            rerank_error = type(exc).__name__
            response_json = {"error": rerank_error}
            logger.warning("Experience reranking failed; using RRF results: %s", rerank_error)
        finally:
            if run_id:
                db.add(
                    ModelCall(
                        run_id=run_id,
                        provider=active_reranker.provider_name,
                        model=active_reranker.model,
                        purpose="retrieval_rerank",
                        prompt_version="rerank-1",
                        latency_ms=int((time.monotonic() - started) * 1000),
                        status=status,
                        request_hash=request_hash,
                        response_json=response_json,
                    )
                )
                db.flush()
    ranked_ids = select_diverse_chunks(
        rerank_candidates if rerank_applied else rrf_ranked_ids,
        rows_by_id,
        limit=limit,
        max_chunks_per_item=settings.rag_max_chunks_per_item,
        context_max_chars=settings.rag_context_max_chars,
    )
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
                "score": rerank_scores.get(identifier, fused[identifier]["score"]),
                "rrf_score": fused[identifier]["score"],
                "rerank_score": rerank_scores.get(identifier),
                "lexical_score": lexical_relevance_score(
                    query,
                    title=rows_by_id[identifier][1].title,
                    heading_path=rows_by_id[identifier][0].heading_path or [],
                    content=rows_by_id[identifier][0].content,
                ),
                "ranks": fused[identifier]["ranks"],
            }
            for identifier in ranked_ids
        ],
        "retrieval_method": (
            "hybrid_rrf_rerank" if rerank_applied and vector_rows
            else "lexical_rerank" if rerank_applied
            else "hybrid_rrf" if vector_rows
            else "lexical"
        ),
        "embedding_error": embedding_error,
        "rerank_applied": rerank_applied,
        "rerank_cache_hit": rerank_cache_hit,
        "rerank_error": rerank_error,
        "rerank_skipped_reason": rerank_skipped_reason,
        "rerank_candidate_count": len(rerank_candidates),
        "rerank_source_count": rerank_source_count,
        "result_count": len(ranked_ids),
        "context_char_count": sum(len(rows_by_id[item][0].content) for item in ranked_ids),
        "searched_at": datetime.now(timezone.utc).isoformat(),
    }
