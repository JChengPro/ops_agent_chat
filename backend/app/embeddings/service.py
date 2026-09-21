from __future__ import annotations

from typing import Protocol
import hashlib
import math

from openai import OpenAI

from app.core.config import get_settings
from app.profiling import measure
from app.cache import cache_key, get_json, set_json


class EmbeddingProvider(Protocol):
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAICompatibleEmbeddingProvider:
    def __init__(self, *, api_key: str, base_url: str, model: str, dimensions: int, batch_size: int, timeout: int) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url.rstrip("/"), timeout=timeout)
        self.model = model
        self.dimensions = dimensions
        self.batch_size = batch_size
        self.cache_identity = [base_url.rstrip("/"), model, dimensions, hashlib.sha256(api_key.encode()).hexdigest()]

    def embed_query(self, text: str, *, project_id: int, environment_id: int | None) -> list[list[float]]:
        ttl = get_settings().embedding_cache_ttl_seconds
        key = cache_key("embedding", [self.cache_identity, project_id, environment_id, text])
        with measure("embedding.cache") as metadata:
            cached = get_json(key) if ttl else None
            valid = (isinstance(cached, list) and len(cached) == self.dimensions
                     and all(type(value) in (int, float) and math.isfinite(value) for value in cached))
            metadata["cache_hit"] = valid
        if valid:
            return [cached]
        vectors = self.embed([text])
        set_json(key, vectors[0], ttl)
        return vectors

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for offset in range(0, len(texts), self.batch_size):
            batch = texts[offset : offset + self.batch_size]
            with measure("embedding.request", model=self.model, batch_size=len(batch)) as metadata:
                response = self.client.embeddings.create(
                    model=self.model,
                    input=batch,
                    dimensions=self.dimensions,
                )
                metadata["input_tokens"] = getattr(getattr(response, "usage", None), "prompt_tokens", None)
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend(list(item.embedding) for item in ordered)
        if len(vectors) != len(texts) or any(len(vector) != self.dimensions for vector in vectors):
            raise RuntimeError("Embedding provider returned an unexpected vector shape")
        return vectors


def configured_embedding_provider() -> EmbeddingProvider | None:
    settings = get_settings()
    if not settings.embedding_configured:
        return None
    return OpenAICompatibleEmbeddingProvider(
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        batch_size=settings.embedding_batch_size,
        timeout=settings.embedding_timeout_seconds,
    )
