"""Rebuild a project's memory from its stored episodes under the extraction rules of today.

The rules that decide what an episode leaves in memory change. What older rules kept then sits
beside what the current ones keep. Synthesizing an episode again replaces what it produced before
(worker.process_episode: memories backed only by that episode go, one also backed by another
episode keeps that evidence; pages and the digest are rewritten), so a rebuild is one extraction
job per stored episode, oldest first. Nothing is deleted and no model is called here: the worker
does both, one episode per transaction.

Three kinds of episode are left as they are:
- one whose text retention has dropped: there is nothing to read again, and its memories would only go;
- one a person's word rests on: it backs a memory a person wrote (remember.py), kept, forgot or
  corrected, or one of the sample, written as given and never extracted. Replacing what the episode
  produced deletes those rows: a kept memory comes back unreviewed or not at all, and the forgotten
  row is what keeps synthesis from learning that memory again (review.matches_forgotten);
- one that already has an extraction job queued or running.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

# Every stored episode of the project and what a rebuild does with it.
_PLAN = """
SELECT e.id, e.occurred_at,
       CASE WHEN e.expired_at IS NOT NULL THEN 'expired'
            WHEN EXISTS (
                 SELECT 1 FROM evidence v JOIN memory_items m ON m.id = v.memory_id
                 WHERE v.project_id = e.project_id AND v.episode_id = e.id
                   AND (m.review = 'kept' OR coalesce(m.origin, 'synthesis') <> 'synthesis' OR m.forgotten_at IS NOT NULL
                        OR EXISTS (SELECT 1 FROM memory_items c WHERE c.id = m.superseded_by AND c.origin = 'person')))
                 THEN 'held'
            WHEN EXISTS (SELECT 1 FROM jobs j WHERE j.project_id = e.project_id AND j.episode_id = e.id
                         AND j.kind = 'extract' AND j.status IN ('queued', 'running')) THEN 'waiting'
            ELSE 'rebuild' END AS state
FROM episodes e WHERE e.project_id = %(p)s
"""
# Background, as imported history is: what people are doing now is synthesized first (worker.claim_job),
# and a long rebuild is not taken for a stuck queue (health.py). Job ids follow the order of the rows.
_QUEUE = """
, queued AS (
    INSERT INTO jobs (project_id, kind, episode_id, background)
    SELECT %(p)s, 'extract', id, true FROM plan WHERE state = 'rebuild' ORDER BY occurred_at, id
)
"""


@dataclass
class Rebuild:
    episodes: int = 0    # stored episodes of the project
    queued: int = 0      # extraction jobs queued (a dry run: that would be)
    waiting: int = 0     # left: an extraction job is already queued or running
    held: int = 0        # left: it backs a memory a person wrote, kept, forgot or corrected (or the sample)
    expired: int = 0     # left: retention dropped the text
    memories: dict[str, int] = field(default_factory=dict)  # memory items before the rebuild, by status


async def rebuild_memory(conn, project_id: UUID, *, dry_run: bool = False) -> Rebuild:
    """Queue every stored episode of the project for synthesis again, oldest first (occurred_at, id).
    `dry_run` counts and changes nothing."""
    states = {r["state"]: r["n"] for r in await (await conn.execute(
        f"WITH plan AS ({_PLAN}){'' if dry_run else _QUEUE} SELECT state, count(*) AS n FROM plan GROUP BY state",
        {"p": project_id})).fetchall()}
    memories = await (await conn.execute(
        "SELECT status, count(*) AS n FROM memory_items WHERE project_id = %s GROUP BY status ORDER BY status",
        (project_id,))).fetchall()
    return Rebuild(episodes=sum(states.values()), queued=states.get("rebuild", 0), waiting=states.get("waiting", 0),
                   held=states.get("held", 0), expired=states.get("expired", 0),
                   memories={r["status"]: r["n"] for r in memories})
