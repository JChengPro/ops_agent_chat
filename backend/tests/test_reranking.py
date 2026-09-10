from dataclasses import dataclass

import pytest

from app.llm.configuration import ResolvedLLMConfiguration
from app.reranking.service import OpenAICompatibleReranker, RerankCache, RerankDocument, RerankResult


@dataclass
class _Message:
    content: str


@dataclass
class _Choice:
    message: _Message


@dataclass
class _Completion:
    choices: list[_Choice]


class _Completions:
    def __init__(self, content: str):
        self.content = content

    def create(self, **kwargs):
        del kwargs
        return _Completion([_Choice(_Message(self.content))])


class _Client:
    def __init__(self, content: str):
        self.chat = type("Chat", (), {"completions": _Completions(content)})()


def _reranker(content: str) -> OpenAICompatibleReranker:
    configuration = ResolvedLLMConfiguration(
        provider="test",
        base_url="https://example.com/v1",
        model="test-model",
        api_key="test-key",
        source="test",
    )
    reranker = OpenAICompatibleReranker(configuration, model="test-model", timeout=5)
    reranker.client = _Client(content)
    return reranker


def test_openai_compatible_reranker_accepts_exact_document_set():
    reranker = _reranker('{"items":[{"id":"doc_1","score":0.9},{"id":"doc_0","score":0.2}]}')

    result = reranker.rerank(
        "query",
        [RerankDocument("doc_0", "first"), RerankDocument("doc_1", "second")],
    )

    assert [(item.id, item.score) for item in result] == [("doc_1", 0.9), ("doc_0", 0.2)]


@pytest.mark.parametrize(
    "payload",
    [
        '{"items":[{"id":"doc_0","score":0.9}]}',
        '{"items":[{"id":"doc_0","score":0.9},{"id":"unknown","score":0.2}]}',
        '{"items":[{"id":"doc_0","score":0.9},{"id":"doc_0","score":0.2}]}',
        '{"items":[{"id":"doc_0","score":2},{"id":"doc_1","score":0.2}]}',
    ],
)
def test_openai_compatible_reranker_rejects_invalid_results(payload):
    with pytest.raises((ValueError, RuntimeError)):
        _reranker(payload).rerank(
            "query",
            [RerankDocument("doc_0", "first"), RerankDocument("doc_1", "second")],
        )


def test_rerank_cache_returns_copy_and_evicts_oldest_entry():
    cache = RerankCache(max_entries=1, ttl_seconds=60)
    original = [RerankResult(id="doc_0", score=0.9)]
    cache.set("first", original)
    hit = cache.get("first")
    assert hit == original and hit is not original

    cache.set("second", [RerankResult(id="doc_1", score=0.8)])
    assert cache.get("first") is None
    assert cache.get("second") == [RerankResult(id="doc_1", score=0.8)]
