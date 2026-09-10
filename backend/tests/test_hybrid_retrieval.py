from sqlalchemy.dialects import postgresql
from types import SimpleNamespace

from app.experience import service
from app.experience.service import lexical_terms, reciprocal_rank_fusion, search_experience
from app.models.agent import ModelCall
from app.reranking.service import RerankResult


class _Rows:
    def __init__(self, rows=None):
        self.rows = rows or []

    def all(self):
        return self.rows


class _RecordingSession:
    def __init__(self, rows=None):
        self.statements = []
        self.added = []
        self.rows = rows or []

    def execute(self, statement):
        self.statements.append(statement)
        return _Rows(self.rows)

    def add(self, value):
        self.added.append(value)

    def flush(self):
        return None


class _EmbeddingProvider:
    dimensions = 1536

    def embed(self, texts):
        return [[0.0] * self.dimensions for _ in texts]


class _ReverseReranker:
    provider_name = "test"
    model = "test-reranker"

    def rerank(self, query, documents):
        del query
        return [RerankResult(id=document.id, score=index / 10) for index, document in enumerate(documents)]


class _FailingReranker:
    provider_name = "test"
    model = "failing-reranker"

    def rerank(self, query, documents):
        del query, documents
        raise TimeoutError("simulated timeout")


class _CountingReranker:
    provider_name = "test"
    model = "cache-test-reranker"

    def __init__(self):
        self.calls = 0

    def rerank(self, query, documents):
        del query
        self.calls += 1
        return [RerankResult(id=document.id, score=1 - index / 10) for index, document in enumerate(documents)]


def _row(identifier: int, title: str):
    chunk = SimpleNamespace(
        id=identifier,
        chunk_key=f"chunk-{identifier}",
        content_hash=f"hash-{identifier}",
        source_ref=f"source-{identifier}",
        heading_path=[title],
        content=f"{title} backend troubleshooting",
    )
    item = SimpleNamespace(id=identifier, title=title, trust_status="verified", source_type="manual")
    return chunk, item


def test_lexical_terms_segment_chinese_and_preserve_technical_tokens():
    terms = lexical_terms("检查 backend 的 SSH 主机指纹")
    assert "backend" in terms
    assert "ssh" in terms
    assert "主机" in terms
    assert "指纹" in terms


def test_rrf_rewards_chunks_seen_by_both_retrievers():
    fused = reciprocal_rank_fusion({"lexical": [1, 2, 3], "vector": [3, 4, 1]})
    assert fused[1]["score"] > fused[2]["score"]
    assert fused[3]["score"] > fused[4]["score"]
    assert fused[1]["ranks"] == {"lexical": 1, "vector": 3}


def test_lexical_search_builds_valid_postgresql_query(monkeypatch):
    monkeypatch.setattr(service, "configured_embedding_provider", lambda: None)
    db = _RecordingSession()

    result = search_experience(db, 7, "检查 backend SSH 主机指纹")

    assert result["retrieval_method"] == "lexical"
    assert len(db.statements) == 1
    compiled = str(db.statements[0].compile(dialect=postgresql.dialect()))
    assert "to_tsvector" in compiled
    assert "plainto_tsquery" in compiled
    assert "ORDER BY" in compiled


def test_vector_search_builds_cosine_distance_query():
    db = _RecordingSession()

    result = search_experience(db, 7, "backend health", embedder=_EmbeddingProvider())

    assert result["retrieval_method"] == "lexical"
    assert len(db.statements) == 2
    compiled = str(db.statements[1].compile(dialect=postgresql.dialect()))
    assert "<=>" in compiled
    assert "embedding IS NOT NULL" in compiled


def test_environment_filter_includes_global_and_selected_environment(monkeypatch):
    monkeypatch.setattr(service, "configured_embedding_provider", lambda: None)
    db = _RecordingSession()

    search_experience(db, 7, "backend", environment_id=9)

    compiled = db.statements[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "experience_items.environment_id IS NULL" in sql
    assert "experience_items.environment_id =" in sql
    assert 9 in compiled.params.values()


def test_reranker_reorders_rrf_candidates(monkeypatch):
    monkeypatch.setattr(service, "configured_embedding_provider", lambda: None)
    db = _RecordingSession([_row(1, "first"), _row(2, "second"), _row(3, "third")])

    result = search_experience(db, 7, "backend", limit=2, reranker=_ReverseReranker())

    assert result["retrieval_method"] == "lexical_rerank"
    assert result["rerank_applied"] is True
    assert [item["item_id"] for item in result["items"]] == [3, 2]
    assert all(item["rerank_score"] is not None for item in result["items"])


def test_agent_rerank_records_model_call(monkeypatch):
    monkeypatch.setattr(service, "configured_embedding_provider", lambda: None)
    db = _RecordingSession([_row(1, "first"), _row(2, "second"), _row(3, "third")])

    search_experience(db, 7, "backend", limit=2, run_id="run-1", reranker=_ReverseReranker())

    calls = [item for item in db.added if isinstance(item, ModelCall)]
    assert len(calls) == 1
    assert calls[0].run_id == "run-1"
    assert calls[0].purpose == "retrieval_rerank"
    assert calls[0].status in {"success", "cached"}


def test_reranker_failure_falls_back_to_rrf(monkeypatch):
    monkeypatch.setattr(service, "configured_embedding_provider", lambda: None)
    db = _RecordingSession([_row(1, "first"), _row(2, "second"), _row(3, "third")])

    result = search_experience(db, 7, "backend", limit=2, reranker=_FailingReranker())

    assert result["retrieval_method"] == "lexical"
    assert result["rerank_applied"] is False
    assert result["rerank_error"] == "TimeoutError"
    assert [item["item_id"] for item in result["items"]] == [1, 2]


def test_small_candidate_set_skips_reranker(monkeypatch):
    monkeypatch.setattr(service, "configured_embedding_provider", lambda: None)
    db = _RecordingSession([_row(1, "first"), _row(2, "second")])

    result = search_experience(db, 7, "backend", limit=5, reranker=_FailingReranker())

    assert result["rerank_applied"] is False
    assert result["rerank_error"] is None
    assert result["rerank_skipped_reason"] == "source_count_not_above_result_limit"


def test_identical_query_and_chunk_versions_reuse_rerank_cache(monkeypatch):
    monkeypatch.setattr(service, "configured_embedding_provider", lambda: None)
    rows = [_row(11, "alpha"), _row(12, "beta"), _row(13, "gamma")]
    reranker = _CountingReranker()

    first = search_experience(_RecordingSession(rows), 701, "unique cache query", limit=2, reranker=reranker)
    second = search_experience(_RecordingSession(rows), 701, "unique cache query", limit=2, reranker=reranker)

    assert first["rerank_cache_hit"] is False
    assert second["rerank_cache_hit"] is True
    assert reranker.calls == 1


def test_lexical_score_rewards_term_coverage_and_heading_matches():
    broad = service.lexical_relevance_score(
        "backend 启动失败",
        title="runbook",
        heading_path=["Backend"],
        content="启动失败排查步骤",
    )
    repeated = service.lexical_relevance_score(
        "backend 启动失败",
        title="runbook",
        heading_path=[],
        content="backend backend backend",
    )
    assert broad > repeated


def test_result_diversity_limits_chunks_from_one_document():
    rows = {
        1: _row(1, "same"),
        2: _row(1, "same"),
        3: _row(3, "other"),
    }
    rows[2][0].id = 2

    selected = service.select_diverse_chunks(
        [1, 2, 3],
        rows,
        limit=3,
        max_chunks_per_item=1,
        context_max_chars=9000,
    )

    assert selected == [1, 3]


def test_result_diversity_respects_context_budget_after_first_chunk():
    rows = {
        1: _row(1, "a" * 700),
        2: _row(2, "b" * 700),
        3: _row(3, "c" * 200),
    }

    selected = service.select_diverse_chunks(
        [1, 2, 3],
        rows,
        limit=3,
        max_chunks_per_item=2,
        context_max_chars=1000,
    )

    assert selected == [1, 3]
