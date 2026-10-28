"""Instant ingest: learn from one small episode within the request and say what was learned.

POST /v1/ingest with `instant: true` stores the episode and queues its job as usual (same
billing), then runs that job here, in the request's transaction, with the worker's own
per-episode processing (so thread pages update too). The job
is marked done in the same transaction, so the worker never runs it again. When it cannot run
now (processing paused, another job of the project running, a model error) the episode simply
stays queued and the response says why.
"""
from __future__ import annotations

import json
import logging
from uuid import UUID

from .edges import related
from .embeddings import Embedder
from .extraction.llm import LLM
from .models import InstantResult, MemoryRelated
from .worker import process_episode

log = logging.getLogger("memvine.instant")
INSTANT_MAX_CHARS = 4_000


async def run_instant(conn, llm: LLM, embedder: Embedder, project_id: UUID, episode_id: str) -> InstantResult:
    """Call inside the ingest transaction, after ingest_episode."""
    project = await (await conn.execute("SELECT processing_paused FROM projects WHERE id = %s", (project_id,))).fetchone()
    if project and project["processing_paused"]:
        return InstantResult(status="queued", reason="Processing is paused for this project; the text is queued.")
    job = await (await conn.execute(
        "SELECT id FROM jobs WHERE project_id = %s AND episode_id = %s AND kind = 'extract' AND status = 'queued' "
        "ORDER BY id DESC LIMIT 1", (project_id, episode_id))).fetchone()
    if job is None:  # unchanged content: nothing to run, say what it already taught
        return InstantResult(status="done", memories=await episode_memories(conn, project_id, episode_id))
    # Jobs of one project run one at a time (worker.run_one holds this lock while one runs).
    locked = (await (await conn.execute(
        "SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS ok", (str(project_id),))).fetchone())["ok"]
    if not locked:
        return InstantResult(status="queued", reason="Memory is tending other sources right now; the text is queued.")
    try:
        async with conn.transaction():  # a savepoint: a failure leaves the episode stored and queued
            result = await process_episode(conn, llm, embedder, project_id, episode_id)
    except Exception as exc:  # noqa: BLE001 - the worker retries the queued job
        log.warning("instant ingest of %s failed, left queued: %s", episode_id, exc)
        return InstantResult(status="queued", reason=f"Could not learn from it now ({str(exc)[:200]}); the text is queued.")
    await conn.execute(
        "UPDATE jobs SET status = 'done', attempts = attempts + 1, claimed_at = now(), finished_at = now(), result = %s "
        "WHERE id = %s", (json.dumps({**result, "instant": True}), job["id"]))
    return InstantResult(status="done", memories=await episode_memories(conn, project_id, episode_id))


async def episode_memories(conn, project_id: UUID, episode_id: str) -> list[MemoryRelated]:
    """Every memory this episode is evidence for, with its edges; a memory follows the ones it
    supersedes or extends, so a reader drawing them in order always has both ends of an edge."""
    rows = await (await conn.execute(
        "SELECT m.id FROM memory_items m WHERE m.project_id = %s AND m.status <> 'retired' "
        "AND EXISTS (SELECT 1 FROM evidence e WHERE e.memory_id = m.id AND e.episode_id = %s) ORDER BY m.created_at, m.statement",
        (project_id, episode_id))).fetchall()
    found = [r for r in [await related(conn, project_id, row["id"]) for row in rows] if r]
    by_id = {m.memory.id: m for m in found}
    ordered: list[MemoryRelated] = []
    placed: set[UUID] = set()

    def place(m: MemoryRelated, path: frozenset) -> None:
        if m.memory.id in placed or m.memory.id in path:
            return
        for e in m.edges:
            if e.direction == "out" and e.item is not None and e.item.id in by_id:
                place(by_id[e.item.id], path | {m.memory.id})
        placed.add(m.memory.id)
        ordered.append(m)

    for m in found:
        place(m, frozenset())
    return ordered
