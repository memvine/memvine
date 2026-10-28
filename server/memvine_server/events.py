"""Memory history and the project's activity feed.

Every change to a memory is one row in memory_events, written by `record` (or `record_many`) in
the same transaction as the change. Writers pass who acted (`actor`):

- 'synthesis': the worker learning from a source;
- 'person:<email or identity>': a signed-in person in the console;
- 'key:<name>': a project key (an agent plugin, an SDK, MCP), an organization key, or
  'key:operator' for the operator key;
- 'system': the server on its own (code staleness, a source deleted, the sample, backfill);
- 'consolidation': the background consolidation job (future).

Events hold ids, kinds, actors and small details, never customer text: evidence quotes and the
statements of related memories are joined when history is read (`history`), so deleting a source
still deletes its text. `detail` keys by convention: `by` (the memory that replaced, extended or
corrected this one), `of` (the memory this one supersedes), `with` (the other side of a
contradiction), `into` / `from` (merges), `related` (any other memory it concerns), `reason`, `from_status`, `files`.

Expired is not stored: an item past its valid_to is computed when its history is read (`history`
appends an 'expired' event dated valid_to), since nothing writes at the moment it expires.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

KINDS = (
    "created",        # a new memory (detail: origin, relation, extends/related)
    "restated",       # a source said it again (reconcile 'same'): evidence added (episode_id)
    "superseded",     # on the old item: replaced by detail.by
    "supersedes",     # on the new item: replaces detail.of
    "contradicted",   # on both items of a contradiction: detail.with
    "extended",       # on the old item: detail.by adds detail to it
    "confirmed",      # a person kept it (Keep / Still true / PATCH confirm)
    "forgotten",      # a person forgot it (retired, never re-learned)
    "restored",       # current again: un-forgotten, or its replacement was forgotten or deleted
    "expired",        # past its valid_to (computed on read, never stored)
    "corrected",      # on the old item: a person said it is wrong; detail.by is the correction
    "merged",         # consolidation: detail.into (on the merged-away item) / detail.from (on the kept one)
    "anchored",       # tied to the code files it names (detail.files)
    "stale",          # its anchored code changed (detail.reason)
    "revalidated",    # stale no more: code matches again, restated, or confirmed
    "category_changed",  # detail.before / detail.after
    "refused",        # synthesis dropped a candidate matching this forgotten memory (detail.reason)
    "deleted",        # its last source was deleted (memory_id NULL; detail.memory_id)
)
SYSTEM = "system"
SYNTHESIS = "synthesis"

_INSERT = ("INSERT INTO memory_events (project_id, memory_id, kind, actor, detail, episode_id) "
           "VALUES (%s, %s, %s, %s, %s, %s)")


def _json(detail: dict | None) -> str:
    return json.dumps(detail or {}, default=str)


async def record(conn, project_id: UUID, kind: str, *, memory_id: UUID | None = None, actor: str = SYSTEM,
                 detail: dict | None = None, episode_id: str | None = None) -> None:
    """Record one event, in the caller's transaction. The one function every writer calls."""
    assert kind in KINDS, kind
    await conn.execute(_INSERT, (project_id, memory_id, kind, actor, _json(detail), episode_id))


async def record_many(conn, project_id: UUID, kind: str, memory_ids: Iterable[UUID], *, actor: str = SYSTEM,
                      detail: dict | None = None, details: dict[UUID, dict] | None = None) -> None:
    """One event of `kind` for each memory, in one statement (for batch updates such as code checks).
    `details` gives a per-memory detail, else `detail` applies to all."""
    await record_batch(conn, project_id, [{"kind": kind, "memory_id": i, "detail": (details or {}).get(i, detail)}
                                          for i in memory_ids], actor=actor)


async def record_batch(conn, project_id: UUID, rows: list[dict], *, actor: str = SYSTEM) -> None:
    """Several events in one statement, in list order: each row a dict with `kind` and optionally
    `memory_id`, `detail`, `episode_id`, and `at` (when it happened, if not now: the sample's
    dated history)."""
    if not rows:
        return
    for r in rows:
        assert r["kind"] in KINDS, r["kind"]
    await conn.execute(
        "INSERT INTO memory_events (project_id, memory_id, kind, actor, detail, episode_id, at) "
        "SELECT %s, m, k, %s, d::jsonb, ep, coalesce(t, now()) "
        "FROM unnest(%s::uuid[], %s::text[], %s::text[], %s::text[], %s::timestamptz[]) WITH ORDINALITY AS u(m, k, d, ep, t, n) "
        "ORDER BY n",
        (project_id, actor, [r.get("memory_id") for r in rows], [r["kind"] for r in rows],
         [_json(r.get("detail")) for r in rows], [r.get("episode_id") for r in rows], [r.get("at") for r in rows]))


def actor_of(request) -> str:
    """Who is calling a project route: 'person:<email>' for a signed-in person, 'key:<name>' for a
    project or organization key, 'key:operator' for the operator key."""
    admin = getattr(request.state, "admin", None)
    principal = getattr(request.state, "principal", None)
    if admin is not None and (principal is None or principal.key_id is None):
        if admin.is_operator:
            return "key:operator"
        return f"person:{admin.user_email}" if admin.user_id else f"key:{admin.key_name}"
    name = getattr(principal, "key_name", None)
    return f"key:{name}" if name else "key:project"


# ------------------------------------------------------------------ reading

EVENT_COLUMNS = "e.id, e.memory_id, e.kind, e.actor, e.detail, e.episode_id, e.at"
_REFS = ("by", "of", "with", "into", "from", "related")


def _ref_ids(rows) -> set[UUID]:
    out = set()
    for r in rows:
        for key in _REFS:
            value = (r["detail"] or {}).get(key)
            if isinstance(value, str):
                try:
                    out.add(UUID(value))
                except ValueError:
                    pass
    return out


async def history(conn, project_id: UUID, memory_id: UUID) -> list[dict] | None:
    """A memory's events, oldest first, with the evidence quotes of created/restated events and the
    statement and status of each memory an event names (detail.by/of/with/into/from/related, as `related`).
    None when the project has no such memory."""
    mem = await (await conn.execute(
        "SELECT id, valid_to, (status IN ('active', 'stale') AND valid_to IS NOT NULL AND valid_to <= now()) AS expired "
        "FROM memory_items WHERE project_id = %s AND id = %s", (project_id, memory_id))).fetchone()
    if not mem:
        return None
    rows = await (await conn.execute(
        f"SELECT {EVENT_COLUMNS} FROM memory_events e WHERE e.project_id = %s AND e.memory_id = %s ORDER BY e.at, e.id",
        (project_id, memory_id))).fetchall()
    quotes: dict[str, list[str]] = {}
    for q in await (await conn.execute(
            "SELECT episode_id, quote FROM evidence WHERE project_id = %s AND memory_id = %s ORDER BY quote",
            (project_id, memory_id))).fetchall():
        quotes.setdefault(q["episode_id"], []).append(q["quote"])
    related = await _statements(conn, project_id, _ref_ids(rows))
    out = []
    for r in rows:
        event = _event(r)
        if r["kind"] in ("created", "restated") and r["episode_id"]:
            event["quotes"] = quotes.get(r["episode_id"], [])
        event["related"] = _related_of(r, related)
        out.append(event)
    if mem["expired"]:
        out.append({"id": None, "memory_id": memory_id, "kind": "expired", "actor": SYSTEM, "detail": {"computed": True},
                    "episode_id": None, "at": mem["valid_to"], "quotes": [], "related": []})
        out.sort(key=lambda e: (e["at"], e["id"] if e["id"] is not None else 2**62))
    return out


async def _statements(conn, project_id: UUID, ids: set[UUID]) -> dict[UUID, dict]:
    if not ids:
        return {}
    rows = await (await conn.execute(
        "SELECT id, statement, status FROM memory_items WHERE project_id = %s AND id = ANY(%s)",
        (project_id, list(ids)))).fetchall()
    return {r["id"]: {"id": r["id"], "statement": r["statement"], "status": r["status"]} for r in rows}


def _related_of(row, statements: dict[UUID, dict]) -> list[dict]:
    out = []
    for key in _REFS:
        value = (row["detail"] or {}).get(key)
        try:
            found = statements.get(UUID(value)) if isinstance(value, str) else None
        except ValueError:
            found = None
        if found:
            out.append({"role": key, **found})
    return out


def _event(r) -> dict:
    return {"id": r["id"], "memory_id": r["memory_id"], "kind": r["kind"], "actor": r["actor"], "detail": r["detail"] or {},
            "episode_id": r["episode_id"], "at": r["at"]}


def encode_cursor(at: datetime, event_id: int) -> str:
    return base64.urlsafe_b64encode(f"{at.isoformat()}|{event_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    """Raises ValueError for a cursor this module did not make."""
    raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    at, _, event_id = raw.rpartition("|")
    return datetime.fromisoformat(at), int(event_id)


async def feed(conn, project_id: UUID, *, since: datetime | None = None, kinds: list[str] | None = None,
               memory_id: UUID | None = None, limit: int = 50, cursor: str | None = None) -> dict:
    """The project's events, newest first: {"events": [...], "next_cursor": str | None}. Each event
    carries the statement of its memory (None when it was deleted)."""
    clauses, params = ["e.project_id = %s"], [project_id]
    if since is not None:
        clauses.append("e.at >= %s")
        params.append(since)
    if kinds:
        clauses.append("e.kind = ANY(%s)")
        params.append(kinds)
    if memory_id is not None:
        clauses.append("e.memory_id = %s")
        params.append(memory_id)
    if cursor:
        at, event_id = decode_cursor(cursor)
        clauses.append("(e.at, e.id) < (%s, %s)")
        params += [at, event_id]
    rows = await (await conn.execute(
        f"SELECT {EVENT_COLUMNS}, m.statement, m.status AS memory_status FROM memory_events e "
        "LEFT JOIN memory_items m ON m.id = e.memory_id "
        f"WHERE {' AND '.join(clauses)} ORDER BY e.at DESC, e.id DESC LIMIT %s",
        [*params, limit + 1])).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    events = [{**_event(r), "statement": r["statement"], "memory_status": r["memory_status"]} for r in rows]
    return {"events": events, "next_cursor": encode_cursor(rows[-1]["at"], rows[-1]["id"]) if more and rows else None}
