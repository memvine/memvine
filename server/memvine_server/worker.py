"""The synthesis worker: takes queued jobs, extracts memory from the episode,
reconciles each candidate against the store, and records what happened.

Runs in-process beside the API (started from the app lifespan) or standalone
via `memvine-server worker`. Jobs are claimed with SKIP LOCKED so several
workers can share one queue. A failed job retries with backoff up to MAX_ATTEMPTS.
"""
from __future__ import annotations

import asyncio
import json
import logging
import traceback
from uuid import UUID

from . import health
from .billing import apply_retention
from .config import settings
from .embeddings import Embedder
from .extraction.llm import LLM, LLMRateLimited
from .ingest import delete_derived
from .passages import index_episode
from .sessions import refresh_state, seal_due, sweep_idle, synthesize_session
from .synthesis import synthesize_episode

log = logging.getLogger("memvine.worker")
MAX_ATTEMPTS = 3
# A job running longer than this is presumed abandoned by a dead worker and requeued.
LEASE_MINUTES = 15
RATE_LIMIT_PAUSE_SECONDS = 300


async def process_episode(conn, llm: LLM, embedder: Embedder, project_id: UUID, episode_id: str) -> dict:
    ep = await (
        await conn.execute(
            "SELECT id, content, occurred_at, label, audience, metadata, code FROM episodes WHERE project_id = %s AND id = %s",
            (project_id, episode_id),
        )
    ).fetchone()
    if not ep:
        return {"skipped": "episode gone"}

    # A retry or re-run replaces what this episode produced before, so it never duplicates:
    # memories backed only by this episode go, memories also backed elsewhere keep that evidence.
    if await (await conn.execute("SELECT 1 FROM evidence WHERE project_id = %s AND episode_id = %s LIMIT 1",
                                 (project_id, ep["id"]))).fetchone():
        await delete_derived(conn, project_id, ep["id"])
    passages = await index_episode(conn, embedder, project_id, ep["id"])
    metadata = ep["metadata"] or {}
    context = await boundary_context(conn, embedder, project_id, ep, metadata)
    project = await (await conn.execute("SELECT id, name, description, memory_instructions FROM projects WHERE id = %s",
                                        (project_id,))).fetchone()
    return {**await synthesize_episode(conn, llm, embedder, project, ep, context), "passages": passages}


async def requeue_abandoned(conn) -> int:
    """Return to the queue every running job whose worker has held it past the lease."""
    rows = await (await conn.execute(
        "UPDATE jobs SET status = 'queued', error = 'requeued: worker lease expired', run_after = now() "
        "WHERE status = 'running' AND (claimed_at IS NULL OR claimed_at < now() - make_interval(mins => %s)) RETURNING id",
        (LEASE_MINUTES,),
    )).fetchall()
    return len(rows)


PREVIOUS_CONTEXT_CHARS = 1_500
RELATED_MEMORIES = 8


async def boundary_context(conn, embedder: Embedder, project_id: UUID, ep, metadata: dict) -> str:
    """What the extractor needs to interpret an episode without extracting it again: the end of
    the conversation just before it (for a session range) and the memories it most resembles."""
    parts: list[str] = []
    session_id, from_seq = metadata.get("session_id"), metadata.get("from_seq")
    if session_id and isinstance(from_seq, int) and from_seq > 1:
        rows = await (await conn.execute(
            "SELECT role, content, at FROM session_events WHERE project_id = %s AND session_id = %s AND seq < %s "
            "ORDER BY seq DESC LIMIT 6", (project_id, session_id, from_seq))).fetchall()
        tail, size = [], 0
        for r in rows:  # newest first; keep the end of what came before
            block = f"## {r['role']} · {r['at'].isoformat()}\n{r['content']}"
            if size + len(block) > PREVIOUS_CONTEXT_CHARS:
                block = block[-(PREVIOUS_CONTEXT_CHARS - size):] if size < PREVIOUS_CONTEXT_CHARS else ""
            if block:
                tail.append(block)
                size += len(block)
            if size >= PREVIOUS_CONTEXT_CHARS:
                break
        if tail:
            parts.append("Earlier in this conversation (already processed):\n" + "\n\n".join(reversed(tail)))
    vector = (await embedder.embed([ep["content"][:2000]]))[0]
    related = await (await conn.execute(
        "SELECT statement, recorded_at FROM memory_items WHERE project_id = %s AND audience = %s AND status = 'active' "
        "AND embedding IS NOT NULL ORDER BY embedding <=> %s::vector LIMIT %s",
        (project_id, ep["audience"], vector, RELATED_MEMORIES))).fetchall()
    if related:
        parts.append("What memory already knows (current values):\n" + "\n".join(
            f"- {r['statement']} ({r['recorded_at'].date().isoformat()})" for r in related))
    return "\n\n".join(parts)


async def claim_job(conn, project_id: UUID | None = None):
    """The oldest runnable job in a project that has no job running.

    A paused project's jobs wait. Live work goes before background work (imported history), so
    what people are doing now becomes memory first. Jobs of one project run one at a time: reconciliation reads what the
    project already knows, and two episodes of the same project reconciled at once
    could each miss the other's facts. Different projects run in parallel.
    """
    return await (
        await conn.execute(
            """
            UPDATE jobs SET status = 'running', attempts = attempts + 1, claimed_at = now()
            WHERE id = (
                SELECT q.id FROM jobs q
                WHERE q.status = 'queued' AND q.run_after <= now()
                  AND (%s::uuid IS NULL OR q.project_id = %s)
                  AND NOT EXISTS (SELECT 1 FROM projects p WHERE p.id = q.project_id AND p.processing_paused)
                  AND (q.kind = 'session_state' OR NOT EXISTS (
                        SELECT 1 FROM jobs r WHERE r.project_id = q.project_id AND r.status = 'running' AND r.kind <> 'session_state'))
                ORDER BY (q.kind = 'session_state') DESC, q.background, q.id FOR UPDATE SKIP LOCKED LIMIT 1
            )
            RETURNING id, project_id, kind, episode_id, attempts
            """,
            (project_id, project_id),
        )
    ).fetchone()


async def run_one(pool, llm: LLM, embedder: Embedder, project_id: UUID | None = None) -> bool:
    """Process at most one job. Returns False when nothing is runnable."""
    async with pool.connection() as conn:
        job = await claim_job(conn, project_id)
        if not job:
            return False
        # Two workers can pass claim_job's check for the same project in the same instant;
        # the advisory lock settles it, and the loser hands its job back for a moment.
        # A state refresh touches only its session row, so it skips the project lock and
        # goes ahead of the queue: a live agent is waiting on it.
        exclusive = job["kind"] != "session_state"
        locked = not exclusive or (await (await conn.execute(
            "SELECT pg_try_advisory_lock(hashtext(%s)) AS ok", (str(job["project_id"]),)
        )).fetchone())["ok"]
        if not locked:
            await conn.execute(
                "UPDATE jobs SET status = 'queued', attempts = attempts - 1, run_after = now() + interval '1 second' WHERE id = %s",
                (job["id"],),
            )
            return True
        try:
            # One transaction per job: a crash mid-episode leaves no half-reconciled state.
            async with conn.transaction():
                if job["kind"] == "extract":
                    result = await process_episode(conn, llm, embedder, job["project_id"], job["episode_id"])
                elif job["kind"] == "session_state":
                    result = await refresh_state(conn, llm, job["project_id"], job["episode_id"])
                elif job["kind"] == "session_close":
                    result = await synthesize_session(conn, job["project_id"], job["episode_id"])
                else:
                    raise ValueError(f"unknown job kind {job['kind']}")
            await conn.execute(
                "UPDATE jobs SET status = 'done', finished_at = now(), result = %s WHERE id = %s",
                (json.dumps(result), job["id"]),
            )
        except asyncio.CancelledError:
            # Shutdown: the transaction rolled back; hand the job back untouched, then stop.
            await asyncio.shield(conn.execute(
                "UPDATE jobs SET status = 'queued', attempts = attempts - 1, run_after = now() WHERE id = %s", (job["id"],)))
            raise
        except LLMRateLimited as exc:
            # Not the job's fault: hand it back without spending an attempt, and pause long
            # enough for a subscription window or a provider's rate limit to clear.
            log.warning("job %s rate limited; retrying in %ss (%s)", job["id"], RATE_LIMIT_PAUSE_SECONDS, type(exc).__name__)
            await conn.execute(
                "UPDATE jobs SET status = 'queued', attempts = attempts - 1, error = %s, run_after = now() + make_interval(secs => %s) WHERE id = %s",
                (str(exc)[:1500], RATE_LIMIT_PAUSE_SECONDS, job["id"]),
            )
        except Exception as exc:  # noqa: BLE001 - every failure is recorded on the job
            # The message can quote model output drawn from the project's text: it is kept on the job
            # (GET /v1/jobs, the project's own), and the shared server log names only the error type.
            log.warning("job %s failed (attempt %s): %s", job["id"], job["attempts"], type(exc).__name__)
            error = f"{exc}\n{traceback.format_exc()[-1500:]}"
            if job["attempts"] >= MAX_ATTEMPTS:
                await conn.execute(
                    "UPDATE jobs SET status = 'failed', finished_at = now(), error = %s WHERE id = %s", (error, job["id"])
                )
            else:
                await conn.execute(
                    "UPDATE jobs SET status = 'queued', error = %s, run_after = now() + make_interval(secs => %s) WHERE id = %s",
                    (error, 30 * 4 ** (job["attempts"] - 1), job["id"]),  # 30 s, then 2 min
                )
        finally:
            if exclusive:
                await conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (str(job["project_id"]),))
        return True


async def drain(pool, llm: LLM, embedder: Embedder, project_id: UUID | None = None) -> int:
    """Process jobs (of one project, or all) until none is runnable. Returns how many ran."""
    count = 0
    while await run_one(pool, llm, embedder, project_id):
        count += 1
    return count


async def _loop(pool, llm: LLM, embedder: Embedder, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await health.beat(pool)  # throttled; tells /v1/health/synthesis a worker is polling
            busy = await run_one(pool, llm, embedder)
        except Exception:
            log.exception("worker loop error")
            busy = False
        if not busy:
            try:
                async with pool.connection() as conn:
                    await sweep_idle(conn)
                    await seal_due(conn)
                    await requeue_abandoned(conn)
                    await apply_retention(conn)
            except Exception:
                log.exception("idle sweep error")
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
            except TimeoutError:
                pass


async def run_forever(pool, llm: LLM, embedder: Embedder, stop: asyncio.Event) -> None:
    """`worker_concurrency` loops share the queue; claim_job keeps each project to one at a time.
    Beside them, the alert check posts broken synthesis to the webhook (health.py)."""
    try:
        await health.beat(pool, force=True)
    except Exception:
        log.exception("worker heartbeat error")
    await asyncio.gather(health.alert_loop(pool, stop),
                         *(_loop(pool, llm, embedder, stop) for _ in range(max(1, settings.worker_concurrency))))
