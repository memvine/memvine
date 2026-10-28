"""Code-change staleness: a memory about the code goes stale when that code changes.

The server never sees a repository. Clients report, with a conversation, the files it touched and
their git blob hashes (`code`: {repo, files: {path: blob}}). When a fact about current code
(semantic or procedural; history and preferences never go stale this way) names one of those
files, it is anchored to that file at that version. Later a client reports the versions it has
now (POST /v1/code/check): a memory whose anchored file differs, or is gone, turns stale with the
reason, and recall says so; when the files match again (an edit was undone), it is active again.
A later conversation that restates the fact, or a person confirming it, anchors it to the
version current then.
"""
from __future__ import annotations

import re
from uuid import UUID

from . import events

STALEABLE_KINDS = ("semantic", "procedural")
# A bare file name counts as a mention only if it is specific: has an extension and isn't tiny.
MIN_NAME_CHARS = 5


def mentioned_files(text: str, files: dict[str, str]) -> dict[str, str]:
    """The files (path -> blob) a text names, by repository path or by a distinctive file name."""
    found: dict[str, str] = {}
    lowered = text.lower()
    for path, blob in files.items():
        name = path.rsplit("/", 1)[-1]
        if path.lower() in lowered:
            found[path] = blob
        elif "." in name and len(name) >= MIN_NAME_CHARS and re.search(rf"(?<![\w/.-]){re.escape(name.lower())}(?![\w-])", lowered):
            found[path] = blob
    return found


async def code_for_episode(conn, project_id: UUID, episode) -> dict | None:
    """The code context of an episode: its own, or for a session range the files the session
    reported (their latest versions)."""
    if episode.get("code"):
        return episode["code"]
    session_id = (episode.get("metadata") or {}).get("session_id")
    if not session_id:
        return None
    rows = await (await conn.execute(
        "SELECT repo, path, blob FROM session_code WHERE project_id = %s AND session_id = %s", (project_id, session_id))).fetchall()
    if not rows:
        return None
    repo = rows[0]["repo"]
    return {"repo": repo, "files": {r["path"]: r["blob"] for r in rows if r["repo"] == repo}}


async def anchor(conn, project_id: UUID, memory_id: UUID, *, kind: str, text: str, code: dict | None, relation: str,
                 actor: str = events.SYNTHESIS) -> int:
    """Anchor a just-reconciled fact to the files it names. A restated fact ('same') is
    re-validated: anchored to the versions of this conversation, and active again if stale."""
    if not code or kind not in STALEABLE_KINDS:
        return 0
    hits = mentioned_files(text, code.get("files") or {})
    for path, blob in hits.items():
        await conn.execute(
            "INSERT INTO memory_anchors (project_id, memory_id, repo, path, blob) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (memory_id, repo, path) DO UPDATE SET blob = EXCLUDED.blob, anchored_at = now()",
            (project_id, memory_id, code["repo"], path, blob))
    if not hits:
        return 0
    rows = [{"kind": "anchored", "memory_id": memory_id, "detail": {"repo": code["repo"], "files": sorted(hits)[:20]}}]
    if relation == "same":
        back = await (await conn.execute(
            "UPDATE memory_items SET status = 'active', stale_reason = NULL WHERE id = %s AND status = 'stale' AND stale_reason IS NOT NULL "
            "RETURNING id", (memory_id,))).fetchone()
        if back:
            rows.append({"kind": "revalidated", "memory_id": memory_id, "detail": {"reason": "restated against the current code"}})
    await events.record_batch(conn, project_id, rows, actor=actor)
    return len(hits)


async def anchored_paths(conn, project_id: UUID, repo: str, limit: int = 2000) -> list[str]:
    rows = await (await conn.execute(
        "SELECT DISTINCT a.path FROM memory_anchors a JOIN memory_items m ON m.id = a.memory_id "
        "WHERE a.project_id = %s AND a.repo = %s AND m.status IN ('active', 'stale') ORDER BY a.path LIMIT %s",
        (project_id, repo, limit))).fetchall()
    return [r["path"] for r in rows]


async def check(conn, project_id: UUID, repo: str, files: dict[str, str | None], *, actor: str = events.SYSTEM) -> dict:
    """Record the versions a client has now, and mark memories stale or fresh against them."""
    for path, blob in files.items():
        await conn.execute(
            "INSERT INTO code_files (project_id, repo, path, blob) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (project_id, repo, path) DO UPDATE SET blob = EXCLUDED.blob, seen_at = now()",
            (project_id, repo, path, blob))
    # Stale: an active memory with an anchor whose reported version differs (or is gone).
    stale = await (await conn.execute(
        """
        UPDATE memory_items m SET status = 'stale', stale_reason = s.reason
        FROM (
            SELECT DISTINCT ON (a.memory_id) a.memory_id,
                   CASE WHEN f.blob IS NULL THEN a.path || ' was deleted' ELSE a.path || ' changed' END
                   || ' since ' || to_char(a.anchored_at, 'FMDD Mon YYYY') AS reason
            FROM memory_anchors a JOIN code_files f ON f.project_id = a.project_id AND f.repo = a.repo AND f.path = a.path
            WHERE a.project_id = %s AND a.repo = %s AND a.path = ANY(%s) AND f.blob IS DISTINCT FROM a.blob
            ORDER BY a.memory_id, a.path
        ) s
        WHERE m.id = s.memory_id AND m.status = 'active'
        RETURNING m.id, s.reason
        """, (project_id, repo, list(files)))).fetchall()
    # Fresh again: a memory made stale by code whose anchored files all match what was reported.
    fresh = await (await conn.execute(
        """
        UPDATE memory_items m SET status = 'active', stale_reason = NULL
        WHERE m.project_id = %s AND m.status = 'stale' AND m.stale_reason IS NOT NULL
          AND EXISTS (SELECT 1 FROM memory_anchors a WHERE a.memory_id = m.id AND a.repo = %s AND a.path = ANY(%s))
          AND NOT EXISTS (
              SELECT 1 FROM memory_anchors a LEFT JOIN code_files f ON f.project_id = a.project_id AND f.repo = a.repo AND f.path = a.path
              WHERE a.memory_id = m.id AND (f.path IS NULL OR f.blob IS DISTINCT FROM a.blob))
        RETURNING m.id
        """, (project_id, repo, list(files)))).fetchall()
    await events.record_batch(
        conn, project_id,
        [{"kind": "stale", "memory_id": r["id"], "detail": {"reason": r["reason"], "repo": repo}} for r in stale]
        + [{"kind": "revalidated", "memory_id": r["id"], "detail": {"reason": "code matches again", "repo": repo}} for r in fresh],
        actor=actor)
    return {"checked": len(files), "stale": [r["id"] for r in stale], "fresh": [r["id"] for r in fresh]}


async def revalidate(conn, project_id: UUID, memory_id: UUID, *, actor: str = events.SYSTEM) -> None:
    """A person confirmed the memory: anchor it to the versions last reported, and clear staleness."""
    await conn.execute(
        "UPDATE memory_anchors a SET blob = f.blob, anchored_at = now() FROM code_files f "
        "WHERE a.memory_id = %s AND f.project_id = a.project_id AND f.repo = a.repo AND f.path = a.path AND f.blob IS NOT NULL",
        (memory_id,))
    back = await (await conn.execute(
        "UPDATE memory_items SET status = 'active', stale_reason = NULL WHERE id = %s AND project_id = %s AND status = 'stale' "
        "RETURNING id", (memory_id, project_id))).fetchone()
    if back:
        await events.record(conn, project_id, "revalidated", memory_id=memory_id, actor=actor, detail={"reason": "confirmed"})
