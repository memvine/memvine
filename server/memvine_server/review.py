"""The review loop: people keep, forget or correct what synthesis learned, and what they forget
teaches the extraction gate.

- Review state. Synthesis writes memory items as `new`; a person's own memory (POST /v1/memories,
  a correction, MCP memory_remember) starts `kept`, and Keep / Still true (PATCH confirm) keeps one.
  New items are served by recall like any other: review is for people, not a filter on agents.
- Forget as feedback. When a person forgets a synthesis-made memory, or says it is wrong and gives
  the correction, a private feedback case records the statement, its category, its evidence quotes
  and an excerpt of the source around the first quote. `memvine-server feedback-export` writes them
  as JSONL for the local gate (benchmark/knowledge/cases.py), which never stores them in the repo.
- Never again. Synthesis drops a candidate that matches a memory a person forgot in this project:
  the same wording, or embeddings at least FORGOTTEN_SIMILARITY alike.
"""
from __future__ import annotations

import json
from uuid import UUID

from . import events
from .extraction.extract import normalize
from .store import retire

EXCERPT_CHARS = 1500
# On BGE-small, restatements of one fact score about 0.9 and different facts about one project
# 0.6-0.7 (reconcile.py); 0.92 drops close rewordings without catching a changed value.
FORGOTTEN_SIMILARITY = 0.92


def excerpt_around(content: str, quote: str, cap: int = EXCERPT_CHARS) -> str:
    """At most `cap` characters of `content`, centred on `quote` when it is found (whitespace and
    case aside), else its beginning."""
    content = content or ""
    if len(content) <= cap:
        return content
    at = content.find(quote) if quote else -1
    if at < 0 and quote:
        at = content.lower().find(quote.strip().lower()[:80])
    if at < 0:
        return content[:cap]
    mid = at + min(len(quote), cap) // 2
    start = max(0, min(mid - cap // 2, len(content) - cap))
    return content[start:start + cap]


async def _case_material(conn, project_id: UUID, memory_id: UUID):
    """The memory and the material a feedback case keeps, or None for a memory not made by synthesis."""
    mem = await (await conn.execute(
        "SELECT id, statement, category, origin FROM memory_items WHERE project_id = %s AND id = %s",
        (project_id, memory_id))).fetchone()
    if not mem or (mem["origin"] or "synthesis") != "synthesis":
        return None
    rows = await (await conn.execute(
        "SELECT v.episode_id, v.quote, e.content, e.expired_at FROM evidence v "
        "JOIN episodes e ON e.project_id = v.project_id AND e.id = v.episode_id "
        "WHERE v.memory_id = %s ORDER BY e.occurred_at, v.episode_id", (memory_id,))).fetchall()
    quotes = [{"episode_id": r["episode_id"], "quote": r["quote"]} for r in rows]
    source = next((r for r in rows if r["expired_at"] is None), None)
    excerpt = excerpt_around(source["content"], source["quote"]) if source else ""
    return mem, quotes, (source["episode_id"] if source else None), excerpt


async def _record(conn, project_id: UUID, memory_id: UUID, kind: str, correction: str | None = None) -> UUID | None:
    material = await _case_material(conn, project_id, memory_id)
    if material is None:
        return None
    mem, quotes, episode_id, excerpt = material
    row = await (await conn.execute(
        "INSERT INTO feedback_cases (project_id, kind, memory_id, statement, category, correction, quotes, episode_id, excerpt) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (project_id, kind, memory_id, mem["statement"], mem["category"], correction, json.dumps(quotes), episode_id, excerpt),
    )).fetchone()
    return row["id"]


async def forget(conn, project_id: UUID, memory_id: UUID, *, actor: str = events.SYSTEM) -> bool:
    """A person forgets a memory: it is retired (store.retire), marked forgotten so synthesis never
    re-creates it, and, when synthesis made it, recorded as a feedback case. False when it was not
    current (nothing forgotten)."""
    if not await retire(conn, project_id, memory_id, actor=actor):
        return False
    await conn.execute("UPDATE memory_items SET forgotten_at = now(), retired_reason = 'forgotten', flag = NULL, "
                       "flag_reason = NULL, flag_related = NULL WHERE project_id = %s AND id = %s", (project_id, memory_id))
    await events.record(conn, project_id, "forgotten", memory_id=memory_id, actor=actor)
    await _record(conn, project_id, memory_id, "forgotten")
    return True


async def restore(conn, project_id: UUID, memory_id: UUID, *, actor: str = events.SYSTEM) -> None:
    """A forgotten memory brought back: no longer forgotten, and its forget is no longer feedback."""
    await events.record(conn, project_id, "restored", memory_id=memory_id, actor=actor)
    await conn.execute("UPDATE memory_items SET forgotten_at = NULL, retired_reason = NULL WHERE project_id = %s AND id = %s",
                       (project_id, memory_id))
    await conn.execute("DELETE FROM feedback_cases WHERE project_id = %s AND memory_id = %s AND kind = 'forgotten'",
                       (project_id, memory_id))


async def record_wrong(conn, project_id: UUID, memory_id: UUID, correction: str) -> UUID | None:
    """A person said a synthesis-made memory is wrong and what is true instead."""
    return await _record(conn, project_id, memory_id, "wrong", correction)


async def keep(conn, project_id: UUID, memory_id: UUID, *, actor: str = events.SYSTEM) -> None:
    await conn.execute("UPDATE memory_items SET review = 'kept' WHERE project_id = %s AND id = %s", (project_id, memory_id))
    await events.record(conn, project_id, "confirmed", memory_id=memory_id, actor=actor)


async def has_forgotten(conn, project_id: UUID) -> bool:
    return bool(await (await conn.execute(
        "SELECT 1 FROM memory_items WHERE project_id = %s AND forgotten_at IS NOT NULL AND status = 'retired' LIMIT 1",
        (project_id,))).fetchone())


async def matches_forgotten(conn, project_id: UUID, statement: str, embedding: list[float]) -> UUID | None:
    """The forgotten memory a candidate restates: the same wording, or cosine >= FORGOTTEN_SIMILARITY."""
    rows = await (await conn.execute(
        "SELECT id, statement, CASE WHEN embedding IS NULL THEN 0 ELSE 1 - (embedding <=> %s::vector) END AS similarity "
        "FROM memory_items WHERE project_id = %s AND forgotten_at IS NOT NULL AND status = 'retired'",
        (embedding, project_id))).fetchall()
    wording = normalize(statement)
    for r in sorted(rows, key=lambda r: -r["similarity"]):
        if r["similarity"] >= FORGOTTEN_SIMILARITY or normalize(r["statement"]) == wording:
            return r["id"]
    return None


CASE_COLUMNS = "id, kind, memory_id, statement, category, correction, quotes, episode_id, excerpt, created_at"


async def list_cases(conn, project_id: UUID, *, limit: int = 500, offset: int = 0) -> list[dict]:
    return list(await (await conn.execute(
        f"SELECT {CASE_COLUMNS} FROM feedback_cases WHERE project_id = %s ORDER BY created_at DESC LIMIT %s OFFSET %s",
        (project_id, limit, offset))).fetchall())
