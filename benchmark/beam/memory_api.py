"""The memory systems the harness can score, behind one interface.

A backend opens one store per conversation, takes the conversation's turns, says when it has
finished processing them, and answers a recall at a moment in time. The harness never looks
inside: the answer model reads whatever `recall` returns, as JSON, the way published BEAM runs hand a
memory system's recall response to the answer model.

    memvine      the Memvine server (MEMVINE_API_URL, MEMVINE_MANAGEMENT_KEY)
    fullcontext  no memory system: every turn up to the question's moment, in order, capped
    rag          plain retrieval over the raw turns: local embeddings + keyword overlap, no synthesis

The two reference arms exist so that a Memvine score always has something to be compared with on
the same harness, prompts, models and judge.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from dataset import Conversation, Message, utc_iso
from log import log

BACKENDS = ("memvine", "fullcontext", "rag")

# One recall: at most this many results, within this many tokens of evidence (the settings of published runs).
RECALL_LIMIT = 100
RECALL_MAX_TOKENS = 8_000
# What of Memvine's recall response the answer model reads: "briefing" (the text an agent is given),
# "results" (the same lines as a list) or "both". Both says everything twice: about 17,600 tokens of
# context where plain search sends 8,700, for the same score (0.893 against 0.894 over 60 questions).
CONTEXT = os.environ.get("BEAM_MEMVINE_CONTEXT", "briefing")

RETRY_DELAYS_SECONDS = (1, 2, 4)
TIMEOUT_SECONDS = 300


class IngestionFailed(RuntimeError):
    """The memory system could not process a conversation. The run stops rather than score a
    conversation whose memory is incomplete."""


class MemoryApiError(RuntimeError):
    def __init__(self, message: str, *, code: str = "", status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def tokens_of(text: str) -> int:
    return max(1, len(text) // 4)


def parse_moment(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class Handle:
    """One conversation's store, as the backend knows it."""
    conversation_id: str
    slug: str = ""
    key: str = ""
    extra: dict = field(default_factory=dict)


# --- Memvine --------------------------------------------------------------------------------

# Consecutive turns of one session are sent together as one episode, up to this many characters,
# so extraction sees a stretch of conversation rather than one line (an answer read without its
# question loses who decided what) while each call stays small enough to extract carefully.
MAX_ITEM_CHARS = 150_000
EPISODE_CHARS = int(os.environ.get("BEAM_EPISODE_CHARS", "6000"))
ITEMS_PER_PUSH = 100
# BEAM is a long conversation with one person, so its projects use Memvine's conversation mode (every
# concrete detail is kept, recall returns details with source excerpts). "coding" measures the
# keep-little mode on the same data.
MEMORY_MODE = os.environ.get("BEAM_MEMORY_MODE", "conversation")
POLL_SECONDS = 3


def episodes_of(conversation: Conversation) -> list[dict]:
    """Turns grouped into episodes: same session, in order, bounded by EPISODE_CHARS."""
    out: list[dict] = []
    group: list[Message] = []

    def flush() -> None:
        if not group:
            return
        first = group[0]
        body = "\n\n".join(f"## {m.author} · {utc_iso(m.timestamp)}\n{m.content}" for m in group)
        # The API takes at most 200,000 characters per item; a longer turn is sent as consecutive parts.
        parts = [body[at : at + MAX_ITEM_CHARS] for at in range(0, len(body), MAX_ITEM_CHARS)]
        for number, part in enumerate(parts):
            out.append({
                "id": f"{conversation.id}_{first.session_id}_{first.ordinal:05d}" + (f"_p{number}" if number else ""),
                "content": part if not number else f"## {first.author} · {utc_iso(first.timestamp)} (continued)\n{part}",
                "timestamp": utc_iso(first.timestamp),
                "identity": conversation.user_identity,
                "label": conversation.title,
                "metadata": {"session": first.session_id, "turns": [m.message_id for m in group]},
            })
        group.clear()

    size = 0
    for message in conversation.messages:
        if group and (message.session_id != group[0].session_id or size + len(message.content) > EPISODE_CHARS):
            flush()
            size = 0
        group.append(message)
        size += len(message.content)
    flush()
    return out


class MemvineBackend:
    name = "memvine"

    def __init__(self, api_url: str, management_key: str, tag: str) -> None:
        self.api_url = api_url.rstrip("/")
        self.tag = tag
        self._management = httpx.AsyncClient(
            base_url=self.api_url, headers={"Authorization": f"Bearer {management_key.strip()}"}, timeout=TIMEOUT_SECONDS,
        )
        self._projects: dict[str, httpx.AsyncClient] = {}

    def project_name(self, conversation_id: str) -> str:
        return f"BEAM {self.tag} {conversation_id}"

    def config(self) -> dict:
        return {"episodeChars": EPISODE_CHARS, "recallBudgetTokens": RECALL_MAX_TOKENS, "memoryMode": MEMORY_MODE}

    async def open(self, conversation: Conversation) -> Handle:
        name = self.project_name(conversation.id)
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
        project = await _call(self._management, "POST", "/v1/projects", {"name": name, "slug": slug, "memory_mode": MEMORY_MODE},
                              accept=(201, 409))
        slug = project.get("slug", slug)
        key = await _call(self._management, "POST", f"/v1/projects/{slug}/keys", {"name": "beam"})
        self._projects[slug] = httpx.AsyncClient(
            base_url=self.api_url, headers={"Authorization": f"Bearer {key['key']}"}, timeout=TIMEOUT_SECONDS,
        )
        log(f"project {slug} created", conversation.id)
        return Handle(conversation.id, slug=slug, key=key["id"])

    async def reopen(self, conversation: Conversation) -> Handle:
        """An earlier run's project for this conversation, found by name."""
        name = self.project_name(conversation.id)
        projects = await _call(self._management, "GET", "/v1/projects")
        match = next((p for p in projects if p["name"] == name), None)
        if not match:
            raise SystemExit(f"No project named {name!r} to reuse")
        key = await _call(self._management, "POST", f"/v1/projects/{match['slug']}/keys", {"name": "beam rescore"})
        self._projects[match["slug"]] = httpx.AsyncClient(
            base_url=self.api_url, headers={"Authorization": f"Bearer {key['key']}"}, timeout=TIMEOUT_SECONDS,
        )
        return Handle(conversation.id, slug=match["slug"], key=key["id"])

    async def ingest(self, handle: Handle, conversation: Conversation) -> None:
        client = self._projects[handle.slug]
        episodes = episodes_of(conversation)
        sent = 0
        for start in range(0, len(episodes), ITEMS_PER_PUSH):
            batch = episodes[start : start + ITEMS_PER_PUSH]
            await _call(client, "POST", "/v1/ingest", {"items": batch}, accept=(202,))
            sent += len(batch)
            log(f"sent {sent}/{len(episodes)} episodes ({len(conversation.messages)} turns)", conversation.id)
        handle.extra["episodes"] = len(episodes)

    async def wait(self, handle: Handle) -> None:
        client = self._projects[handle.slug]
        while True:
            jobs = await _call(client, "GET", "/v1/jobs?limit=1")
            counts = jobs.get("counts") or {}
            if counts.get("failed"):
                detail = await _call(client, "GET", "/v1/jobs?limit=500")
                failed = next((j for j in detail["jobs"] if j["status"] == "failed"), {})
                raise IngestionFailed(f"{counts['failed']} job(s) failed in {handle.slug}: {str(failed.get('error'))[:300]}")
            if not counts.get("queued") and not counts.get("running"):
                return
            await asyncio.sleep(POLL_SECONDS)

    async def recall(self, handle: Handle, query: str, as_of: str, identity: str) -> dict:
        client = self._projects[handle.slug]
        response = await _call(client, "POST", "/v1/recall", {
            "query": query, "as_of": as_of, "budget": RECALL_MAX_TOKENS, "identity": identity,
        })
        results = [{
            "content": item["memory"]["statement"],
            "occurredAt": item["memory"]["recorded_at"],
            "validFrom": item["memory"]["valid_from"],
            "validTo": item["memory"]["valid_to"],
            "kind": item["memory"]["kind"],
            "status": item["memory"]["status"],
            "assertion": item["memory"]["assertion"],
            **({"previousValue": item["previous"]} if item.get("previous") else {}),
        } for item in response["items"][:RECALL_LIMIT]]
        excerpts = [{"content": p["content"], "occurredAt": p["occurred_at"], "kind": "source_excerpt"}
                    for p in response.get("passages") or []]
        digests = [{"content": d["content"], "from": d["starts_at"], "occurredAt": d["occurred_at"], "kind": "digest"}
                   for d in response.get("digests") or []]
        return {
            "asOf": as_of,
            **({"briefing": response["briefing"]} if CONTEXT in ("briefing", "both") else {}),
            **({"results": digests + results + excerpts} if CONTEXT in ("results", "both") else {}),
            "resultCount": len(digests + results + excerpts),
            "usedEvidenceTokens": response["tokens"],
        }

    async def close(self) -> None:
        for client in self._projects.values():
            await client.aclose()
        await self._management.aclose()


async def _call(client: httpx.AsyncClient, method: str, path: str, body=None, accept: tuple[int, ...] = (200, 201)) -> dict:
    attempts = len(RETRY_DELAYS_SECONDS) + 1
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            response = await client.request(method, path, json=body)
        except httpx.HTTPError as error:
            if last:
                raise
            log(f"{method} {path} failed ({type(error).__name__}); retrying in {RETRY_DELAYS_SECONDS[attempt]}s")
            await asyncio.sleep(RETRY_DELAYS_SECONDS[attempt])
            continue
        if (response.status_code == 429 or response.status_code >= 500) and not last:
            log(f"{method} {path} returned {response.status_code}; retrying in {RETRY_DELAYS_SECONDS[attempt]}s")
            await asyncio.sleep(RETRY_DELAYS_SECONDS[attempt])
            continue
        if response.status_code not in accept and response.is_error:
            code = ""
            try:
                code = str((response.json().get("error") or {}).get("code") or "")
            except ValueError:
                pass
            raise MemoryApiError(f"{method} {path} returned {response.status_code}: {response.text[:1000]}",
                                 code=code, status=response.status_code)
        return response.json() if response.content else {}
    raise AssertionError("unreachable")


# --- Full context ------------------------------------------------------------------------------

CONTEXT_CHARS = int(os.environ.get("BEAM_CONTEXT_CHARS", "1200000"))


class FullContextBackend:
    """Every turn up to the moment asked, oldest first, as a reference arm with no memory system.
    The answer model gets the conversation itself, so this is the ceiling when the whole thing fits
    its context and the honest baseline when it does not (the oldest turns are dropped first)."""

    name = "fullcontext"

    def __init__(self) -> None:
        self._turns: dict[str, list[Message]] = {}

    def config(self) -> dict:
        return {"contextChars": CONTEXT_CHARS}

    async def open(self, conversation: Conversation) -> Handle:
        return Handle(conversation.id)

    async def ingest(self, handle: Handle, conversation: Conversation) -> None:
        self._turns[handle.conversation_id] = list(conversation.messages)

    async def wait(self, handle: Handle) -> None:
        return None

    async def recall(self, handle: Handle, query: str, as_of: str, identity: str) -> dict:
        moment = parse_moment(as_of)
        turns = [m for m in self._turns[handle.conversation_id] if m.timestamp <= moment]
        kept: list[Message] = []
        size = 0
        for m in reversed(turns):  # keep the most recent turns when the cap bites
            if size + len(m.content) > CONTEXT_CHARS:
                break
            kept.append(m)
            size += len(m.content)
        kept.reverse()
        results = [{"content": f"{m.author}: {m.content}", "occurredAt": utc_iso(m.timestamp)} for m in kept]
        return {"asOf": as_of, "results": results, "usedEvidenceTokens": size // 4,
                "truncated": len(kept) < len(turns)}

    async def close(self) -> None:
        return None


# --- Plain RAG ---------------------------------------------------------------------------------

CHUNK_CHARS = 1_500
RAG_CANDIDATES = 60
RRF_K = 60
_WORD = re.compile(r"[a-z0-9]+")
STOP = set("the a an and or of to in on for with is are was were be been it this that at by as from he she they we i you your my our their his her its not do does did what when who how which".split())


@dataclass
class Chunk:
    conversation_id: str
    text: str
    occurred_at: datetime
    ordinal: int
    vector: list[float] | None = None
    words: Counter = field(default_factory=Counter)


def chunk_message(m: Message) -> list[str]:
    text = f"{m.author}: {m.content}"
    if len(text) <= CHUNK_CHARS:
        return [text]
    pieces, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if current and len(current) + len(sentence) > CHUNK_CHARS:
            pieces.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def words_of(text: str) -> Counter:
    return Counter(w for w in _WORD.findall(text.lower()) if w not in STOP and len(w) > 1)


class RagBackend:
    """Retrieval over the raw turns with no synthesis: local embeddings (BGE-small, as the Memvine
    server uses) fused with keyword overlap, packed into the same evidence budget. The arm that
    says whether synthesis earns its cost."""

    name = "rag"

    def __init__(self) -> None:
        self._chunks: dict[str, list[Chunk]] = {}
        self._model = None
        self._lock = asyncio.Lock()

    def config(self) -> dict:
        return {"chunkChars": CHUNK_CHARS, "candidates": RAG_CANDIDATES, "recallBudgetTokens": RECALL_MAX_TOKENS,
                "embeddings": "BAAI/bge-small-en-v1.5"}

    def _embedder(self):
        if self._model is None:
            from fastembed import TextEmbedding
            self._model = TextEmbedding("BAAI/bge-small-en-v1.5")
        return self._model

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        model = self._embedder()
        async with self._lock:  # the model is not safe to share across threads
            return await asyncio.to_thread(lambda: [v.tolist() for v in model.embed(texts)])

    async def open(self, conversation: Conversation) -> Handle:
        return Handle(conversation.id)

    async def ingest(self, handle: Handle, conversation: Conversation) -> None:
        chunks: list[Chunk] = []
        for m in conversation.messages:
            for piece in chunk_message(m):
                chunks.append(Chunk(conversation.id, piece, m.timestamp, len(chunks), words=words_of(piece)))
        vectors = await self._embed([c.text for c in chunks])
        for chunk, vector in zip(chunks, vectors, strict=False):
            chunk.vector = vector
        self._chunks[handle.conversation_id] = chunks
        log(f"embedded {len(chunks)} chunks from {len(conversation.messages)} turns", conversation.id)

    async def wait(self, handle: Handle) -> None:
        return None

    async def recall(self, handle: Handle, query: str, as_of: str, identity: str) -> dict:
        moment = parse_moment(as_of)
        chunks = [c for c in self._chunks[handle.conversation_id] if c.occurred_at <= moment]
        if not chunks:
            return {"asOf": as_of, "results": [], "usedEvidenceTokens": 0}
        qvec = (await self._embed([query]))[0]
        qwords = words_of(query)

        by_vector = sorted(chunks, key=lambda c: -dot(qvec, c.vector or []))[:RAG_CANDIDATES]
        by_words = sorted(chunks, key=lambda c: -overlap(qwords, c.words))[:RAG_CANDIDATES]
        scores: dict[int, float] = {}
        for ranked in (by_vector, by_words):
            for position, chunk in enumerate(ranked):
                if ranked is by_words and overlap(qwords, chunk.words) == 0:
                    continue
                scores[chunk.ordinal] = scores.get(chunk.ordinal, 0.0) + 1.0 / (RRF_K + position + 1)
        ordered = sorted(scores.items(), key=lambda kv: -kv[1])

        picked, spent = [], 0
        for ordinal, score in ordered:
            chunk = chunks_by_ordinal(chunks, ordinal)
            cost = tokens_of(chunk.text)
            if spent + cost > RECALL_MAX_TOKENS or len(picked) >= RECALL_LIMIT:
                break
            spent += cost
            picked.append((score, chunk))
        results = [{"content": c.text, "occurredAt": utc_iso(c.occurred_at), "score": round(s, 5)} for s, c in picked]
        return {"asOf": as_of, "results": results, "usedEvidenceTokens": spent}

    async def close(self) -> None:
        return None


def chunks_by_ordinal(chunks: list[Chunk], ordinal: int) -> Chunk:
    # Chunks are stored in ordinal order and filtered by time, so a scan from the end is short.
    for chunk in chunks:
        if chunk.ordinal == ordinal:
            return chunk
    raise KeyError(ordinal)


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False)) if a and b else 0.0


def overlap(query: Counter, doc: Counter) -> float:
    if not query or not doc:
        return 0.0
    hits = sum(min(n, doc[w]) for w, n in query.items() if w in doc)
    return hits / math.sqrt(sum(doc.values()))


# --- Factory ------------------------------------------------------------------------------------

def make_backend(name: str, tag: str):
    if name == "memvine":
        url, key = os.environ.get("MEMVINE_API_URL", "").strip(), os.environ.get("MEMVINE_MANAGEMENT_KEY", "").strip()
        if not url or not key:
            raise SystemExit("Set MEMVINE_API_URL and MEMVINE_MANAGEMENT_KEY for the memvine backend")
        return MemvineBackend(url, key, tag)
    if name == "fullcontext":
        return FullContextBackend()
    if name == "rag":
        return RagBackend()
    raise SystemExit(f"unknown backend {name!r}; choose from {', '.join(BACKENDS)}")
