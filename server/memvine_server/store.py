"""Row-level helpers for memory items and evidence, shared by the worker,
reconciliation, recall and the HTTP routes."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from .extraction.extract import Candidate
from .models import Evidence, MemoryOut

MEMORY_COLUMNS = """
    id, kind, statement, subjects, valid_from, valid_to, recorded_at, status,
    supersedes, superseded_by, assertion, confidence, audience, conflicts_with, standing, stale_reason,
    category, why, thread_id, scope, review, origin
"""


async def insert_item(conn, project_id: UUID, cand: Candidate, embedding: list[float], *, recorded_at: datetime,
                      episode_id: str, supersedes: UUID | None = None, audience: str = "project",
                      origin: str = "synthesis", review: str = "new") -> UUID:
    """`origin`: who made it (synthesis, person, sample); `review`: 'new' until a person keeps it."""
    row = await (
        await conn.execute(
            f"""
            INSERT INTO memory_items
                (project_id, kind, statement, subjects, valid_from, recorded_at, assertion, confidence, embedding, supersedes, audience, standing,
                 category, why, origin, review)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING {MEMORY_COLUMNS}
            """,
            (
                project_id, cand.kind, cand.statement, cand.subjects, cand.valid_from or recorded_at, recorded_at,
                cand.assertion, cand.confidence, embedding, supersedes, audience, cand.standing,
                cand.category, cand.why, origin, review,
            ),
        )
    ).fetchone()
    await add_evidence(conn, project_id, row["id"], episode_id, cand.quote)
    return row["id"]


async def add_evidence(conn, project_id: UUID, memory_id: UUID, episode_id: str, quote: str) -> None:
    await conn.execute(
        "INSERT INTO evidence (memory_id, project_id, episode_id, quote) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (memory_id, project_id, episode_id, quote),
    )


async def evidence_for(conn, memory_ids: list[UUID]) -> dict[UUID, list[Evidence]]:
    if not memory_ids:
        return {}
    rows = await (
        await conn.execute(
            "SELECT v.memory_id, v.episode_id, v.quote, e.identity FROM evidence v "
            "LEFT JOIN episodes e ON e.project_id = v.project_id AND e.id = v.episode_id "
            "WHERE v.memory_id = ANY(%s) ORDER BY v.episode_id",
            (memory_ids,),
        )
    ).fetchall()
    out: dict[UUID, list[Evidence]] = {mid: [] for mid in memory_ids}
    for r in rows:
        out[r["memory_id"]].append(Evidence(episode_id=r["episode_id"], quote=r["quote"], identity=r["identity"]))
    return out


def memory_out(row, evidence: list[Evidence] | None = None) -> MemoryOut:
    return MemoryOut(
        id=row["id"], kind=row["kind"], statement=row["statement"], subjects=list(row["subjects"] or []),
        valid_from=row["valid_from"], valid_to=row["valid_to"], recorded_at=row["recorded_at"], status=row["status"],
        supersedes=row["supersedes"], superseded_by=row["superseded_by"], assertion=row["assertion"],
        confidence=row["confidence"], audience=row["audience"], evidence=evidence or [],
        conflicts_with=list(row.get("conflicts_with") or []),
        standing=bool(row.get("standing")),
        stale_reason=row.get("stale_reason"),
        category=row.get("category"),
        why=row.get("why"),
        thread_id=row.get("thread_id"),
        scope=row.get("scope"),
        review=row.get("review") or "kept",
        origin=row.get("origin"),
    )


async def retire(conn, project_id: UUID, memory_id: UUID) -> bool:
    """Forget a memory: it is retired (never recalled again, its evidence kept). When it had replaced
    an older fact that is still marked as replaced by it, that fact is current again: forgetting a
    wrong update leaves what was known before it. False when there is no such current memory
    (a superseded one is history and stays as it is)."""
    row = await (await conn.execute(
        "UPDATE memory_items SET status = 'retired', valid_to = now() WHERE project_id = %s AND id = %s "
        "AND status IN ('active', 'stale') RETURNING supersedes", (project_id, memory_id))).fetchone()
    if not row:
        return False
    if row["supersedes"]:
        await conn.execute(
            "UPDATE memory_items SET status = 'active', valid_to = NULL, superseded_by = NULL "
            "WHERE project_id = %s AND id = %s AND status = 'superseded' AND superseded_by = %s",
            (project_id, row["supersedes"], memory_id))
    return True


async def get_memory(conn, project_id: UUID, memory_id: UUID) -> MemoryOut | None:
    row = await (
        await conn.execute(
            f"SELECT {MEMORY_COLUMNS} FROM memory_items WHERE project_id = %s AND id = %s", (project_id, memory_id)
        )
    ).fetchone()
    if not row:
        return None
    ev = await evidence_for(conn, [memory_id])
    return memory_out(row, ev[memory_id])


async def list_memories(conn, project_id: UUID, *, status: str | None, kind: str | None, q: str | None,
                        limit: int, offset: int, category: str | None = None, thread_id: UUID | None = None,
                        scope: str | None = None, review: str | None = None) -> list[MemoryOut]:
    clauses, params = ["project_id = %s"], [project_id]
    if review == "new":  # awaiting review: current items no person has kept yet
        clauses.append("review = 'new' AND status IN ('active', 'stale')")
    elif review:
        clauses.append("review = %s")
        params.append(review)
    if scope == "project":  # items stored before scopes existed are project-wide
        clauses.append("scope IS DISTINCT FROM 'thread'")
    elif scope:
        clauses.append("scope = %s")
        params.append(scope)
    if status:
        clauses.append("status = %s")
        params.append(status)
    if kind:
        clauses.append("kind = %s")
        params.append(kind)
    if category:
        clauses.append("category = %s")
        params.append(category)
    if thread_id:
        clauses.append("thread_id = %s")
        params.append(thread_id)
    if q:
        clauses.append("(tsv @@ websearch_to_tsquery('english', %s) OR statement ILIKE %s)")
        params += [q, f"%{q}%"]
    params += [limit, offset]
    rows = await (
        await conn.execute(
            f"SELECT {MEMORY_COLUMNS} FROM memory_items WHERE {' AND '.join(clauses)} "
            "ORDER BY recorded_at DESC LIMIT %s OFFSET %s",
            params,
        )
    ).fetchall()
    ev = await evidence_for(conn, [r["id"] for r in rows])
    return [memory_out(r, ev[r["id"]]) for r in rows]
