from sqlalchemy.dialects import postgresql

from app.experience import service
from app.experience.service import lexical_terms, reciprocal_rank_fusion, search_experience


class _Rows:
    def all(self):
        return []


class _RecordingSession:
    def __init__(self):
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        return _Rows()


class _EmbeddingProvider:
    dimensions = 1536

    def embed(self, texts):
        return [[0.0] * self.dimensions for _ in texts]


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
