"""Session memory: the working set of one live agent session.

Turns stream in as events. A fast LLM pass keeps a small state current (goal,
decisions, open questions, what is in play, recent facts, the assistant's own
progress claims kept apart). After a compaction or reset the agent asks for a
brief: its state rendered as text, then long-term memory relevant to its goal.
When the session goes idle, its turns become episodes and enter the
extraction pipeline like any other source.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from .access import audiences_for
from .checkpoints import due_sessions, seal_session
from .config import settings
from .embeddings import Embedder
from .extraction.extract import parse_json
from .extraction.llm import LLM, LLMError, fast
from .extraction.prompts import SESSION_STATE_SYSTEM, session_state_user
from .models import KeptMemory, RecallRequest, SessionEventsRequest, SessionKept, SessionOut, SessionState
from .recall import KIND_WORD, STANDING_HEADER, recall

STATE_TURNS_WINDOW = 24  # turns handed to the state pass, besides the previous state

SESSION_COLUMNS = """
    id, agent, identity, label, status, started_at, last_event_at, turns, state, state_through,
    state_updated_at, synthesized_at, sealed_through, processed_through
"""


def session_out(row) -> SessionOut:
    state = row["state"] if isinstance(row["state"], dict) else json.loads(row["state"] or "{}")
    return SessionOut(**{k: row[k] for k in row if k != "state"}, state=SessionState(**{k: v for k, v in state.items() if k in SessionState.model_fields}))


async def get_session(conn, project_id: UUID, session_id: str) -> SessionOut | None:
    row = await (
        await conn.execute(f"SELECT {SESSION_COLUMNS} FROM sessions WHERE project_id = %s AND id = %s", (project_id, session_id))
    ).fetchone()
    return session_out(row) if row else None


async def list_sessions(conn, project_id: UUID, limit: int) -> list[SessionOut]:
    rows = await (
        await conn.execute(
            f"SELECT {SESSION_COLUMNS} FROM sessions WHERE project_id = %s ORDER BY last_event_at DESC LIMIT %s",
            (project_id, limit),
        )
    ).fetchall()
    return [session_out(r) for r in rows]


async def append_events(conn, project_id: UUID, session_id: str, body: SessionEventsRequest) -> SessionOut:
    """Store the events, reopen the session if it was synthesized, and queue a state refresh
    when enough turns have arrived since the last one."""
    now = datetime.now(UTC)
    times = [e.at for e in body.events if e.at]
    first, last = (min(times), max(times)) if (times and not body.live) else (now, now)
    await conn.execute(
        """
        INSERT INTO sessions (project_id, id, agent, identity, label, status, started_at, last_event_at, imported)
        VALUES (%s, %s, %s, %s, %s, 'live', %s, %s, %s)
        ON CONFLICT (project_id, id) DO UPDATE SET
            imported = sessions.imported AND EXCLUDED.imported,
            agent = COALESCE(EXCLUDED.agent, sessions.agent),
            identity = COALESCE(EXCLUDED.identity, sessions.identity),
            label = COALESCE(EXCLUDED.label, sessions.label),
            status = 'live', synthesized_at = NULL, last_event_at = GREATEST(sessions.last_event_at, EXCLUDED.last_event_at)
        """,
        (project_id, session_id, body.agent, body.identity, body.label, first, last, not body.live),
    )
    row = await (
        await conn.execute(
            "SELECT coalesce(max(seq), 0) AS seq FROM session_events WHERE project_id = %s AND session_id = %s",
            (project_id, session_id),
        )
    ).fetchone()
    seq = row["seq"]
    for event in body.events:
        seq += 1
        await conn.execute(
            "INSERT INTO session_events (project_id, session_id, seq, role, content, at) VALUES (%s, %s, %s, %s, %s, %s)",
            (project_id, session_id, seq, event.role, event.content, event.at or now),
        )
    if body.code:
        for path, blob in body.code.files.items():
            await conn.execute(
                "INSERT INTO session_code (project_id, session_id, repo, path, blob) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (project_id, session_id, repo, path) DO UPDATE SET blob = EXCLUDED.blob, seen_at = now()",
                (project_id, session_id, body.code.repo, path, blob))
    session = await (
        await conn.execute(
            f"UPDATE sessions SET turns = %s WHERE project_id = %s AND id = %s RETURNING {SESSION_COLUMNS}",
            (seq, project_id, session_id),
        )
    ).fetchone()
    if body.live and seq - session["state_through"] >= settings.session_state_every_turns:
        await enqueue_state_refresh(conn, project_id, session_id)
    # Rolling checkpoint: a completed stretch past the size threshold becomes an episode now.
    result = await seal_session(conn, project_id, session_id, final=False, now=now)
    if result.get("sealed"):
        return await get_session(conn, project_id, session_id)
    return session_out(session)


async def enqueue_state_refresh(conn, project_id: UUID, session_id: str) -> None:
    await conn.execute(
        """
        INSERT INTO jobs (project_id, kind, episode_id)
        SELECT %s, 'session_state', %s
        WHERE NOT EXISTS (SELECT 1 FROM jobs WHERE project_id = %s AND kind = 'session_state' AND episode_id = %s AND status IN ('queued', 'running'))
        """,
        (project_id, session_id, project_id, session_id),
    )


def render_turns(rows) -> str:
    return "\n\n".join(f"[{r['seq']}] {r['role']} · {r['at'].isoformat()}\n{r['content']}" for r in rows)


async def refresh_state(conn, llm: LLM, project_id: UUID, session_id: str) -> dict:
    """The fast pass: previous state plus the turns since it, into a new state."""
    session = await (
        await conn.execute("SELECT state, state_through, turns FROM sessions WHERE project_id = %s AND id = %s", (project_id, session_id))
    ).fetchone()
    if not session:
        return {"skipped": "session gone"}
    since = max(session["state_through"], session["turns"] - STATE_TURNS_WINDOW)
    turns = await (
        await conn.execute(
            "SELECT seq, role, content, at FROM session_events WHERE project_id = %s AND session_id = %s AND seq > %s ORDER BY seq",
            (project_id, session_id, since),
        )
    ).fetchall()
    if not turns:
        return {"skipped": "no new turns"}
    previous = session["state"] if isinstance(session["state"], dict) else json.loads(session["state"] or "{}")
    raw = await fast(llm).complete(SESSION_STATE_SYSTEM, session_state_user(json.dumps(previous, ensure_ascii=False), render_turns(turns)), max_tokens=1500)
    try:
        data = parse_json(raw)
        if not isinstance(data, dict):
            raise TypeError("expected a JSON object")
        state = SessionState(**{k: v for k, v in data.items() if k in SessionState.model_fields})
    except (ValueError, TypeError) as exc:
        raise LLMError(f"session state was not valid JSON: {exc}") from exc
    through = turns[-1]["seq"]
    await conn.execute(
        "UPDATE sessions SET state = %s, state_through = %s, state_updated_at = now() WHERE project_id = %s AND id = %s",
        (state.model_dump_json(), through, project_id, session_id),
    )
    return {"state_through": through, "turns_read": len(turns)}


def render_state(session: SessionOut) -> str:
    s = session.state
    lines = ["=== memvine · this session's working state ==="]
    if s.goal:
        lines.append(f"Goal: {s.goal}")
    for title, items in (("Decisions", s.decisions), ("Open questions", s.open_questions), ("In play", s.in_play),
                         ("Recent facts", s.recent_facts), ("Assistant's unverified progress claims", s.progress_claims)):
        if items:
            lines.append(f"{title}:")
            lines.extend(f"- {item}" for item in items)
    if session.state_through < session.turns:
        lines.append(f"(state reflects turns up to {session.state_through} of {session.turns})")
    return "\n".join(lines)


async def brief(conn, embedder: Embedder, project_id: UUID, session: SessionOut, budget: int) -> tuple[str, int]:
    """The session's state, then long-term memory relevant to its goal within the remaining budget."""
    text = render_state(session)
    spent = max(1, len(text) // 4)
    query = " ".join([session.state.goal, *session.state.in_play[:3], *session.open_questions_for_recall()]) if session.state.goal else ""
    if query.strip() and budget - spent > 200:
        audiences = await audiences_for(conn, project_id, session.identity) if session.identity else ["project"]
        recalled = await recall(conn, embedder, project_id, RecallRequest(query=query[:1000], budget=budget - spent), audiences)
        if not recalled.abstained:
            text = f"{text}\n\n{recalled.briefing}"
            spent += recalled.tokens
        elif recalled.standing:  # nothing relevant to the goal, but the project's rules still apply
            section = "\n".join([STANDING_HEADER, *(f"- {s}" for s in recalled.standing)])
            text = f"{text}\n\n{section}"
            spent += max(1, len(section) // 4)
    return text, spent


# SessionOut is a pydantic model; a small helper keeps brief() readable.
def _open_questions_for_recall(self: SessionOut) -> list[str]:
    return self.state.open_questions[:2]


SessionOut.open_questions_for_recall = _open_questions_for_recall  # type: ignore[attr-defined]


async def synthesize_session(conn, project_id: UUID, session_id: str) -> dict:
    """End a session: seal everything not yet sealed (the ranges already sealed keep their ids
    and are not re-extracted) and mark it synthesized."""
    result = await seal_session(conn, project_id, session_id, final=True, now=datetime.now(UTC))
    if "skipped" in result:
        return result
    await conn.execute(
        "UPDATE sessions SET status = 'synthesized', synthesized_at = now() WHERE project_id = %s AND id = %s", (project_id, session_id)
    )
    return {"episodes": len(result["sealed"]), "sealed": result["sealed"]}


async def sweep_idle(conn) -> int:
    """Queue synthesis for every live session idle for session_idle_minutes. Returns how many."""
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.session_idle_minutes)
    rows = await (
        await conn.execute(
            """
            INSERT INTO jobs (project_id, kind, episode_id, background)
            SELECT s.project_id, 'session_close', s.id, s.imported FROM sessions s
            WHERE s.status = 'live' AND s.last_event_at < %s AND s.turns > 0
              AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.project_id = s.project_id AND j.kind = 'session_close'
                              AND j.episode_id = s.id AND j.status IN ('queued', 'running'))
            RETURNING id
            """,
            (cutoff,),
        )
    ).fetchall()
    return len(rows)


async def seal_due(conn) -> int:
    """Age-triggered checkpoints for live sessions whose oldest unsealed message has waited."""
    now = datetime.now(UTC)
    sealed = 0
    for project_id, session_id in await due_sessions(conn, now):
        async with conn.transaction():
            result = await seal_session(conn, project_id, session_id, final=False, now=now)
        sealed += len(result.get("sealed", []))
    return sealed


async def delete_session(conn, project_id: UUID, session_id: str) -> dict | None:
    """Delete a session and everything drawn from it: its messages, the sources (episodes) sealed
    from it, the memories only those sources support (memories also backed elsewhere keep that
    evidence), its pending jobs and reported code. None when there is no such session."""
    from .ingest import delete_episode

    found = await (await conn.execute(
        "SELECT 1 FROM sessions WHERE project_id = %s AND id = %s FOR UPDATE", (project_id, session_id))).fetchone()
    if not found:
        return None
    prefix = f"session:{session_id}:"
    episodes = [r["id"] for r in await (await conn.execute(
        "SELECT id FROM episodes WHERE project_id = %s AND starts_with(id, %s)", (project_id, prefix))).fetchall()]
    memories_before = (await (await conn.execute(
        "SELECT count(*) AS n FROM memory_items WHERE project_id = %s", (project_id,))).fetchone())["n"]
    for episode_id in episodes:
        await delete_episode(conn, project_id, episode_id)
    memories_after = (await (await conn.execute(
        "SELECT count(*) AS n FROM memory_items WHERE project_id = %s", (project_id,))).fetchone())["n"]
    await conn.execute(
        "DELETE FROM jobs WHERE project_id = %s AND status <> 'running' AND (episode_id = %s OR starts_with(episode_id, %s))",
        (project_id, session_id, prefix))
    await conn.execute("DELETE FROM session_code WHERE project_id = %s AND session_id = %s", (project_id, session_id))
    events = await (await conn.execute(
        "DELETE FROM session_events WHERE project_id = %s AND session_id = %s RETURNING seq", (project_id, session_id))).fetchall()
    await conn.execute("DELETE FROM sessions WHERE project_id = %s AND id = %s", (project_id, session_id))
    return {"messages": len(events), "sources": len(episodes), "memories": memories_before - memories_after}


# ------------------------------------------------------------------ what a session left in memory

# The order a summary names kinds of memory in; anything else follows.
KEPT_ORDER = ["decision", "rule", "gotcha", "fact", "preference", "procedure", "plan", "event"]


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _and(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def kept_summary(kept: list[KeptMemory], superseded: int, contradictions: int) -> str:
    """"Memory kept 2 decisions and 1 gotcha; 1 fact superseded." """
    counts: dict[str, int] = {}
    for k in kept:
        counts[k.category] = counts.get(k.category, 0) + 1
    order = sorted(counts, key=lambda c: (KEPT_ORDER.index(c) if c in KEPT_ORDER else len(KEPT_ORDER), c))
    text = f"Memory kept {_and([_count(counts[c], c) for c in order])}" if kept else "Memory kept nothing new"
    tail = []
    if superseded:
        tail.append(f"{_count(superseded, 'fact')} superseded")
    if contradictions:
        tail.append(f"{_count(contradictions, 'contradiction')} to settle")
    return f"{text}{'; ' + ', '.join(tail) if tail else ''}."


async def kept(conn, project_id: UUID, session_id: str) -> SessionKept | None:
    """GET /v1/sessions/{id}/kept: the memories this session added, as opposed to ones it only
    restated. A memory counts when its earliest source is one of the session's (a restatement adds
    evidence to an older memory and does not count). Forgotten (retired) and since-superseded ones
    are left out, so the answer stays true after "forget the last one"."""
    session = await get_session(conn, project_id, session_id)
    if not session:
        return None
    prefix = f"session:{session_id}:"
    tending = session.status == "live" or bool(await (await conn.execute(
        """
        SELECT 1 FROM jobs WHERE project_id = %s AND status IN ('queued', 'running')
          AND ((kind = 'extract' AND starts_with(episode_id, %s)) OR (kind = 'session_close' AND episode_id = %s)) LIMIT 1
        """, (project_id, prefix, session_id))).fetchone())
    rows = await (await conn.execute(
        """
        SELECT m.id, m.kind, m.category, m.statement, m.supersedes, m.conflicts_with, old.statement AS previous
        FROM memory_items m LEFT JOIN memory_items old ON old.id = m.supersedes
        WHERE m.project_id = %s AND m.status IN ('active', 'stale')
          AND m.id IN (SELECT memory_id FROM evidence WHERE project_id = %s AND starts_with(episode_id, %s))
          AND starts_with((SELECT e.episode_id FROM evidence e JOIN episodes ep ON ep.project_id = e.project_id AND ep.id = e.episode_id
                           WHERE e.memory_id = m.id ORDER BY ep.created_at, ep.occurred_at, e.episode_id LIMIT 1), %s)
        ORDER BY m.recorded_at, m.created_at
        """, (project_id, project_id, prefix, prefix))).fetchall()
    items = [KeptMemory(id=r["id"], category=r["category"] or KIND_WORD.get(r["kind"], "fact"), statement=r["statement"],
                        supersedes=r["supersedes"], previous=r["previous"]) for r in rows]
    superseded = len({r["supersedes"] for r in rows if r["supersedes"]})
    contradictions = sum(1 for r in rows if r["conflicts_with"])
    return SessionKept(session_id=session_id, tending=tending, kept=items, superseded=superseded,
                       contradictions=contradictions, summary=kept_summary(items, superseded, contradictions))
