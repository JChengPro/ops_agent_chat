from types import SimpleNamespace

from app.embeddings.service import OpenAICompatibleEmbeddingProvider


def test_openai_compatible_embedding_requests_configured_dimensions():
    provider = OpenAICompatibleEmbeddingProvider(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-embedding",
        dimensions=1536,
        batch_size=20,
        timeout=10,
    )
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            data=[SimpleNamespace(index=0, embedding=[0.0] * 1536)],
        )

    provider.client = SimpleNamespace(embeddings=SimpleNamespace(create=create))

    vectors = provider.embed(["document"])

    assert captured["dimensions"] == 1536
    assert len(vectors[0]) == 1536


def test_openai_compatible_embedding_batches_large_document_indexes():
    provider = OpenAICompatibleEmbeddingProvider(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-embedding",
        dimensions=1536,
        batch_size=20,
        timeout=10,
    )
    batch_lengths = []

    def create(**kwargs):
        batch_lengths.append(len(kwargs["input"]))
        return SimpleNamespace(
            data=[SimpleNamespace(index=index, embedding=[float(index)] * 1536) for index, _ in enumerate(kwargs["input"])],
        )

    provider.client = SimpleNamespace(embeddings=SimpleNamespace(create=create))

    vectors = provider.embed([f"chunk-{index}" for index in range(42)])

    assert batch_lengths == [20, 20, 2]
    assert len(vectors) == 42
