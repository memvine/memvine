"""Second-stage ranking for conversation-mode recall.

A long conversation leaves thousands of details that all look alike to the embedding model: asked
for "the deadline for the initial deployment", every dated plan in the store is close, and the one
line that answers can sit past the hundredth place in both the vector and the keyword list. A
cross-encoder reads the question and one candidate together, so it can tell them apart. Recall hands
it the fused candidates and takes its order (recall.py).

The model runs locally through fastembed (about 150 MB, downloaded once), like the embedding model.
Coding-mode recall does not use it: there the fast model picks what a task needs (recall.py: gate).
"""
from __future__ import annotations

import asyncio
from typing import Protocol

from .config import settings


class Reranker(Protocol):
    async def scores(self, query: str, texts: list[str]) -> list[float]: ...


class FastEmbedReranker:
    def __init__(self, model: str) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self._model = TextCrossEncoder(model)

    async def scores(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        # CPU-bound and synchronous, like the embedder: keep it off the event loop.
        return [float(s) for s in await asyncio.to_thread(lambda: list(self._model.rerank(query, texts)))]


_reranker: Reranker | None = None
_loaded = False


def get_reranker() -> Reranker | None:
    """The process's reranker, loaded on first use; None when it is turned off (no model named, or
    the fake embeddings backend that tests run on)."""
    global _reranker, _loaded
    if not _loaded:
        _loaded = True
        if settings.recall_rerank_model and settings.embeddings_backend == "fastembed":
            _reranker = FastEmbedReranker(settings.recall_rerank_model)
    return _reranker


def set_reranker(reranker: Reranker | None) -> None:
    """Replace the process's reranker (tests)."""
    global _reranker, _loaded
    _reranker, _loaded = reranker, True
