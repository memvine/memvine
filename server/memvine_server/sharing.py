"""Sharing one memory from a person's own project with a team space on the same server (the console's
Share button; migration 0014). The plugins do the same over two connections, because a person's
project and the team's may be on different servers (plugins/claude-code: `share`).

What crosses is one memory: its statement, what kind of thing it is, who said it, when, and the one
quoted line it rests on. The team space reconciles it like anything said to it: a repeat adds this
person as support to the memory the team has, and a memory that replaces one this person shared
before supersedes the team's copy of that one, with no judge asked.

Each request is confined to one project in Postgres (db.request_project, migrations/rls.sql), so the
two halves run on connections of their own: the person's project, then the team space.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import UUID

from . import billing, review
from .auth import Admin, find_project
from .db import request_project
from .models import BUILTIN_CATEGORIES
from .remember import SupersedeError, remember
from .store import get_memory


class ShareError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@asynccontextmanager
async def confined(pool, project: str):
    """A connection confined to `project` ('' for none), whatever the request is confined to."""
    token = request_project.set(project)
    try:
        async with pool.connection() as conn:
            yield conn
    finally:
        request_project.reset(token)


def _source(memory) -> tuple[str | None, str | None]:
    """Who said it and in what words: the first of its evidence that carries a quote."""
    evidence = next((e for e in memory.evidence if e.quote), None)
    return (evidence.identity, evidence.quote) if evidence else (None, None)


async def _team_space(pool, admin: Admin, slug: str):
    async with confined(pool, "") as conn:
        project = await find_project(conn, admin, slug)
        if project:
            row = await (await conn.execute("SELECT team_space, name FROM projects WHERE id = %s", (project["id"],))).fetchone()
            if row["team_space"]:
                return {**project, "name": row["name"]}
    raise ShareError(404, "not-found", "No such team space")


async def share(pool, llm, embedder, admin: Admin, project_id: UUID, memory_id: UUID, team_slug: str, *, actor: str) -> dict:
    """Copy one memory of `project_id` to the team space `team_slug`, and mark the person's own."""
    async with confined(pool, str(project_id)) as conn:
        own = await (await conn.execute("SELECT team_space FROM projects WHERE id = %s", (project_id,))).fetchone()
        if own["team_space"]:
            raise ShareError(400, "team-space", "This memory is already in a team space")
        memory = await get_memory(conn, project_id, memory_id)
        if not memory:
            raise ShareError(404, "not-found", "No such memory")
        if memory.status not in ("active", "stale"):
            raise ShareError(400, "memory-not-current", f"That memory is {memory.status}; only a current memory can be shared")
        if memory.shared_ref:
            raise ShareError(409, "already-shared", "That memory is already shared with the team")
        replaces = None
        if memory.supersedes:
            old = await (await conn.execute("SELECT shared_ref FROM memory_items WHERE project_id = %s AND id = %s",
                                            (project_id, memory.supersedes))).fetchone()
            replaces = old["shared_ref"] if old else None
    team = await _team_space(pool, admin, team_slug)
    if team["id"] == project_id:
        raise ShareError(400, "team-space", "This memory is already in that team space")
    said_by, quote = _source(memory)
    fields = dict(statement=memory.statement, kind=memory.kind, subjects=memory.subjects[:3], assertion=memory.assertion,
                  standing=memory.standing or None, why=memory.why,
                  category=memory.category if memory.category in BUILTIN_CATEGORIES else None,
                  said_by=said_by or admin.user_email, said_at=memory.valid_from, quote=quote, actor=actor)
    try:
        async with confined(pool, str(team["id"])) as conn:
            decision = None
            if replaces:
                # What this memory replaced was shared: the team's copy of that one is replaced too.
                try:
                    async with conn.transaction():
                        decision = await remember(conn, llm, embedder, team["id"], **fields, supersedes=UUID(replaces),
                                                  given={"kind", "subjects", "category"})
                except (SupersedeError, ValueError):
                    decision = None  # the team's copy is gone or already replaced: share it as a memory of its own
            if decision is None:
                async with conn.transaction():
                    decision = await remember(conn, llm, embedder, team["id"], **fields)
    except billing.CapReached as exc:
        raise ShareError(429, "project-cap-reached", str(exc)) from exc
    async with confined(pool, str(project_id)) as conn:
        await conn.execute("UPDATE memory_items SET shared_ref = %s, shared_at = now() WHERE project_id = %s AND id = %s",
                           (str(decision.memory_id), project_id, memory_id))
        memory = await get_memory(conn, project_id, memory_id)
    return {"memory": memory, "team": team["slug"], "team_memory_id": decision.memory_id, "relation": decision.relation}


async def unshare(pool, admin: Admin, project_id: UUID, memory_id: UUID, *, actor: str) -> dict:
    """Take a shared memory back. This person's support is removed from the team's memory, which is
    forgotten there when nothing else supports it: what a teammate also said stays."""
    async with confined(pool, str(project_id)) as conn:
        memory = await get_memory(conn, project_id, memory_id)
        if not memory:
            raise ShareError(404, "not-found", "No such memory")
        if not memory.shared_ref:
            raise ShareError(400, "not-shared", "That memory is not shared")
    said_by, quote = _source(memory)
    taken_back, team_slug = False, None
    try:
        ref = UUID(memory.shared_ref)
    except ValueError:
        ref = None  # shared by a plugin with a team space on another server
    team = None
    if ref:
        async with confined(pool, "") as conn:
            row = await (await conn.execute(
                "SELECT p.id, p.slug FROM memory_items m JOIN projects p ON p.id = m.project_id WHERE m.id = %s AND p.team_space", (ref,))).fetchone()
            team = row if row and await find_project(conn, admin, row["slug"]) else None
    if team:
        team_slug = team["slug"]
        async with confined(pool, str(team["id"])) as conn, conn.transaction():
            mine = await (await conn.execute(
                "SELECT v.episode_id FROM evidence v JOIN episodes e ON e.project_id = v.project_id AND e.id = v.episode_id "
                "WHERE v.project_id = %s AND v.memory_id = %s AND starts_with(v.episode_id, 'shared:') "
                "AND e.identity IS NOT DISTINCT FROM %s AND v.quote = %s",
                (team["id"], ref, said_by or admin.user_email, quote or memory.statement[:200]))).fetchall()
            episodes = [r["episode_id"] for r in mine]
            if episodes:
                await conn.execute("DELETE FROM evidence WHERE project_id = %s AND memory_id = %s AND episode_id = ANY(%s)",
                                   (team["id"], ref, episodes))
                await conn.execute(
                    "DELETE FROM episodes e WHERE e.project_id = %s AND e.id = ANY(%s) AND NOT EXISTS "
                    "(SELECT 1 FROM evidence v WHERE v.project_id = e.project_id AND v.episode_id = e.id)", (team["id"], episodes))
            left = await (await conn.execute("SELECT count(*) AS n FROM evidence WHERE project_id = %s AND memory_id = %s",
                                             (team["id"], ref))).fetchone()
            if episodes and left["n"] == 0:
                taken_back = await review.forget(conn, team["id"], ref, actor=actor)
    async with confined(pool, str(project_id)) as conn:
        await conn.execute("UPDATE memory_items SET shared_ref = NULL, shared_at = NULL WHERE project_id = %s AND id = %s",
                           (project_id, memory_id))
        memory = await get_memory(conn, project_id, memory_id)
    return {"memory": memory, "team": team_slug, "taken_back": taken_back}
