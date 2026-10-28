"""Store incoming episodes and queue their synthesis.

Re-sending an id with the same content is a no-op. Re-sending it with new
content replaces the episode and deletes everything derived from the old text
(evidence cascades; a memory left with no evidence is removed), then re-queues.
"""
from __future__ import annotations

import hashlib
import json
from uuid import UUID

from .access import touch_identity
from .models import EpisodeIn, IngestItemResult
from .threads import forget_episode


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def delete_derived(conn, project_id: UUID, episode_id: str) -> int:
    """Remove memories drawn only from this episode, and its evidence on thread pages.
    Returns how many memories were deleted."""
    await forget_episode(conn, project_id, episode_id)
    await conn.execute(
        "DELETE FROM evidence WHERE project_id = %s AND episode_id = %s", (project_id, episode_id)
    )
    deleted = await (
        await conn.execute(
            """
            WITH orphans AS (
                SELECT m.id FROM memory_items m
                WHERE m.project_id = %s AND NOT EXISTS (SELECT 1 FROM evidence e WHERE e.memory_id = m.id)
            ), restored AS (
                -- A superseded item whose successor is being deleted becomes current again.
                UPDATE memory_items SET status = 'active', superseded_by = NULL, valid_to = NULL
                WHERE superseded_by IN (SELECT id FROM orphans) AND status = 'superseded'
            )
            DELETE FROM memory_items WHERE id IN (SELECT id FROM orphans) RETURNING id
            """,
            (project_id,),
        )
    ).fetchall()
    await conn.execute(
        "DELETE FROM jobs WHERE project_id = %s AND episode_id = %s AND status = 'queued'", (project_id, episode_id)
    )
    return len(deleted)


async def ingest_episode(conn, project_id: UUID, ep: EpisodeIn) -> IngestItemResult:
    # The audience is part of what was sent: moving an episode to another audience re-derives it.
    # Project-visible episodes hash their content alone, as they always have.
    digest = content_hash(ep.content if ep.audience == "project" else f"{ep.audience}\n{ep.content}")
    existing = await (
        await conn.execute(
            "SELECT content_hash FROM episodes WHERE project_id = %s AND id = %s", (project_id, ep.id)
        )
    ).fetchone()
    if existing and existing["content_hash"] == digest:
        return IngestItemResult(id=ep.id, outcome="unchanged")

    outcome = "queued"
    if existing:
        await delete_derived(conn, project_id, ep.id)
        outcome = "replaced"

    await conn.execute(
        """
        INSERT INTO episodes (project_id, id, content, content_hash, occurred_at, identity, label, metadata, audience, code)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (project_id, id) DO UPDATE SET
            content = EXCLUDED.content, content_hash = EXCLUDED.content_hash, occurred_at = EXCLUDED.occurred_at,
            identity = EXCLUDED.identity, label = EXCLUDED.label, metadata = EXCLUDED.metadata, audience = EXCLUDED.audience,
            code = EXCLUDED.code
        """,
        (project_id, ep.id, ep.content, digest, ep.timestamp, ep.identity, ep.label, json.dumps(ep.metadata), ep.audience,
         ep.code.model_dump_json() if getattr(ep, "code", None) else None),
    )
    if ep.identity:
        await touch_identity(conn, project_id, ep.identity, origin="ingest")
    await conn.execute(
        "INSERT INTO jobs (project_id, kind, episode_id, background) VALUES (%s, 'extract', %s, %s)",
        (project_id, ep.id, bool(getattr(ep, "background", False)))
    )
    return IngestItemResult(id=ep.id, outcome=outcome)


async def delete_episode(conn, project_id: UUID, episode_id: str) -> bool:
    await delete_derived(conn, project_id, episode_id)
    row = await (
        await conn.execute(
            "DELETE FROM episodes WHERE project_id = %s AND id = %s RETURNING id", (project_id, episode_id)
        )
    ).fetchone()
    return row is not None
