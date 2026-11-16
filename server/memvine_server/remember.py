"""Storing one fact directly, without extraction: the REST `POST /v1/memories` route and the MCP
`memory_remember` tool. The statement is kept as its own source episode (so it has evidence and
can be deleted like any source) and reconciled against what is already known.

With `supersedes`, a person has said this replaces a specific fact ("Wrong: what is true
instead?"): no judge is asked, the new item supersedes that one and takes over its kind,
subjects, category, thread, scope, audience and standing unless they are given.

A memory shared from a person's own project (a team space, migration 0014) arrives the same way
with `said_by`, `said_at` and `quote`: who said it, when, and the words it rests on. The source
episode is then those words under that person's name and date, and nothing else from the session
they came from.

When the project is itself a team space the judge reads only the memory, so `quote` must stand
on its own."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from . import events
from .billing import charge_ingest
from .embeddings import Embedder
from .extraction.extract import Candidate, is_standing, normalize_subject
from .extraction.llm import LLM
from .ingest import ingest_episode
from .models import EpisodeIn
from .reconcile import Decision, reconcile
from .review import record_wrong
from .store import insert_item


class SupersedeError(Exception):
    """The fact named in `supersedes` cannot be replaced: missing (404) or no longer current (400)."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


async def superseded_target(conn, project_id: UUID, memory_id: UUID, readable: list[str] | None = None):
    """The fact a correction replaces: one of the project's, current (active or stale), and in an
    audience the caller may read (`readable`; None means any)."""
    old = await (await conn.execute(
        "SELECT id, kind, subjects, category, thread_id, scope, audience, standing, status FROM memory_items "
        "WHERE project_id = %s AND id = %s", (project_id, memory_id))).fetchone()
    if not old or (readable is not None and old["audience"] not in readable):
        raise SupersedeError(404, "not-found", "No such memory to supersede")
    if old["status"] not in ("active", "stale"):
        raise SupersedeError(400, "memory-not-current",
                             f"That memory is {old['status']}; only an active or stale memory can be superseded")
    return old


async def remember(conn, llm: LLM, embedder: Embedder, project_id: UUID, *, statement: str, kind: str, subjects: list[str],
                   assertion: str, audience: str = "project", source: str = "api", standing: bool | None = None,
                   category: str | None = None, why: str | None = None, supersedes: UUID | None = None,
                   readable: list[str] | None = None, given: set[str] | None = None, actor: str = events.SYSTEM,
                   said_by: str | None = None, said_at: datetime | None = None, quote: str | None = None) -> Decision:
    """`actor`: who said it, for the memory's history (events.actor_of). `given`: the fields the
    caller set explicitly (None: all of them); with `supersedes`, the
    others come from the replaced fact. `readable`: the audiences the caller may read, for
    checking `supersedes` (None: any)."""
    statement = statement.strip()
    old = await superseded_target(conn, project_id, supersedes, readable) if supersedes else None
    if old is not None:
        given = {"kind", "subjects", "audience", "category"} if given is None else given
        if "audience" in given and audience != old["audience"]:
            raise SupersedeError(400, "audience-mismatch",
                                 f"A fact only supersedes one in its own audience; that memory is in {old['audience']!r}")
        audience = old["audience"]
        kind = kind if "kind" in given else old["kind"]
        subjects = subjects if "subjects" in given else list(old["subjects"] or [])
        category = category if "category" in given else old["category"]
        if standing is None:
            standing = bool(old["standing"])

    now = datetime.now(UTC)
    quote = (quote or "").strip() or None
    shared = bool(said_by or said_at or quote)
    if shared:
        source = "shared"
    said_at = min(said_at, now) if said_at else None  # a date in the future is a clock that is wrong
    episode_id = f"{source}:{uuid4().hex[:12]}"
    await charge_ingest(conn, project_id, "ingest", len(statement) + len(quote or ""))
    cand = Candidate(
        statement=statement, kind=kind, subjects=[normalize_subject(s) for s in subjects if s.strip()][:3],
        assertion=assertion, confidence="medium", valid_from=said_at, quote=quote or statement[:200],
        standing=is_standing(standing, kind, statement), category=category, why=why,
    )
    await ingest_episode(conn, project_id, EpisodeIn(
        id=episode_id, content=quote or statement, timestamp=said_at or now, identity=said_by,
        label=f"shared by {said_by}" if shared and said_by else "shared" if shared else f"remembered via {source}",
        metadata={"source": source}, audience=audience,
    ))
    # The statement is its own source; the item is written directly, so no extraction job runs.
    await conn.execute("DELETE FROM jobs WHERE project_id = %s AND episode_id = %s AND status = 'queued'", (project_id, episode_id))
    if old is None:
        decision = await reconcile(conn, llm, embedder, project_id, cand, recorded_at=now, episode_id=episode_id, audience=audience,
                                   actor=actor)
        # A person said it: the item is theirs and kept (a restatement keeps the item it merged into).
        await conn.execute(
            "UPDATE memory_items SET review = 'kept', origin = CASE WHEN %s THEN 'person' ELSE origin END WHERE id = %s",
            (decision.relation != "same", decision.memory_id))
        return decision
    decision = await _supersede(conn, embedder, project_id, cand, old, recorded_at=now, episode_id=episode_id, audience=audience,
                                actor=actor)
    # "Wrong": when synthesis made the replaced fact, that is feedback for extraction.
    await record_wrong(conn, project_id, old["id"], statement)
    return decision


async def _supersede(conn, embedder: Embedder, project_id: UUID, cand: Candidate, old, *, recorded_at: datetime,
                     episode_id: str, audience: str, actor: str) -> Decision:
    """Reconcile's `update`, decided by the person instead of the judge: the new item supersedes the
    old one, which keeps its history (status superseded, superseded_by, valid_to). A person's own
    correction carries high confidence, and stays in the old fact's thread and scope."""
    cand.confidence = "high"
    embedding = (await embedder.embed([cand.statement]))[0]
    new_id = await insert_item(conn, project_id, cand, embedding, recorded_at=recorded_at, episode_id=episode_id,
                               supersedes=old["id"], audience=audience, origin="person", review="kept")
    await conn.execute("UPDATE memory_items SET thread_id = %s, scope = %s WHERE id = %s",
                       (old["thread_id"], old["scope"], new_id))
    await conn.execute(
        """
        UPDATE memory_items
        SET status = 'superseded', superseded_by = %s, valid_to = COALESCE(valid_to, %s)
        WHERE id = %s
        """,
        (new_id, recorded_at, old["id"]),
    )
    await events.record_batch(conn, project_id, [
        {"kind": "created", "memory_id": new_id, "detail": {"relation": "correction", "related": old["id"]}, "episode_id": episode_id},
        {"kind": "supersedes", "memory_id": new_id, "detail": {"of": old["id"], "correction": True}},
        {"kind": "corrected", "memory_id": old["id"], "detail": {"by": new_id}},
    ], actor=actor)
    return Decision("update", new_id, old["id"])
