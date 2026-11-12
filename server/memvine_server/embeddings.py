"""Sentence embeddings for memory statements and recall queries.

fastembed runs BGE-small locally (384 dimensions, ~130 MB downloaded once), so
the server works with no paid embedding key. The fake backend is deterministic
and cheap for tests. Both produce unit vectors, so cosine distance in pgvector
is the right metric.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import re
from typing import Protocol

from .config import settings

DIMENSIONS = 384


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedEmbedder:
    # Recall's default relevance threshold (cosine). BGE-small compresses similarities: off-topic
    # questions still score about 0.40-0.47 against a project's memories, on-topic ones 0.64-0.80.
    min_relevance = 0.55
    # Reconciliation: the least similar pair in which one item may replace the other (reconcile.py).
    update_min_similarity = 0.72

    def __init__(self) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding("BAAI/bge-small-en-v1.5")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # The model is CPU-bound and synchronous; keep it off the event loop.
        vectors = await asyncio.to_thread(lambda: list(self._model.embed(texts)))
        return [v.tolist() for v in vectors]


FAKE_STOPWORDS = frozenset(
    "a an and are as at be by did do does for from had has have how i in is it its me my of on or our so that the "
    "their them they this to was we were what when where which who why will with you your".split())


class FakeEmbedder:
    """Bag-of-words hashed into 384 buckets. Shares no semantics with a real
    model, but overlapping words give overlapping vectors, which is all the
    tests need to exercise ranking. Function words are left out (unless a text
    has nothing else), so "the" and "is" alone do not make two texts similar and
    an unrelated question really scores 0, as recall's abstention needs."""

    # No shared content word scores 0 (barring a hash collision); one shared word scores about 0.15 in a
    # sentence-long memory and up to 0.5 in a short one.
    min_relevance = 0.15

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * DIMENSIONS
            words = re.findall(r"[a-z0-9]+", text.lower())
            for word in [w for w in words if w not in FAKE_STOPWORDS] or words:
                bucket = int(hashlib.md5(word.encode()).hexdigest(), 16) % DIMENSIONS
                vec[bucket] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


def make_embedder(backend: str | None = None) -> Embedder:
    backend = backend or settings.embeddings_backend
    if backend == "fastembed":
        return FastEmbedEmbedder()
    if backend == "fake":
        return FakeEmbedder()
    raise ValueError(f"unknown embeddings backend: {backend}")
