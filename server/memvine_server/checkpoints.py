"""Rolling checkpoints: turning a live session's messages into episodes while it runs.

Every message is stored the moment it arrives (session_events). Separately, completed stretches
of conversation are *sealed* into episodes: a fixed message range with a stable id that is never
re-cut, queued for extraction in the same transaction. A range is sealed when

  - its text crosses a soft size threshold, at the next completed exchange (a user message and
    the assistant's reply; never between a tool call and its result, or a question and its answer),
  - its oldest message has waited `max_age` (measured from that message, so a slow but steady
    conversation is still processed), or
  - the session ends, which flushes everything left.

A token cap bounds what one extraction reads; it is the only reason to cut inside an exchange,
and a single message larger than the cap becomes an episode of its own.

`sessions.sealed_through` is the last sealed message; `sessions.processed_through` the last
message whose extraction has committed (advanced inside the extraction transaction).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from .config import settings
from .ingest import ingest_episode
from .models import EpisodeIn


@dataclass(frozen=True)
class Event:
    seq: int
    role: str
    content: str
    at: datetime


def tokens_of(text: str) -> int:
    # Code and tool output tokenize denser than prose; 3.5 characters a token errs on the safe side.
    return int(len(text) / 3.5) + 1


def completed_after(events: list[Event], i: int) -> bool:
    """True when a cut right after events[i] leaves no exchange half-finished: an assistant
    message (or tool result answered by one) followed by the next user message."""
    return events[i].role == "assistant" and i + 1 < len(events) and events[i + 1].role == "user"


def plan_cuts(events: list[Event], *, now: datetime, final: bool, soft_chars: int | None = None,
              max_tokens: int | None = None, max_age: timedelta | None = None) -> list[tuple[int, int]]:
    """Index ranges [start, end] (inclusive) of `events` to seal now, in order. Pure: no I/O."""
    soft_chars = soft_chars or settings.session_soft_chars
    max_tokens = max_tokens or settings.session_max_tokens
    max_age = max_age or timedelta(minutes=settings.session_max_age_minutes)
    cuts: list[tuple[int, int]] = []
    start, n = 0, len(events)
    while start < n:
        chars = tokens = 0
        last_boundary = None
        cut = None
        for i in range(start, n):
            chars += len(events[i].content)
            tokens += tokens_of(events[i].content)
            if tokens > max_tokens:
                # Over the cap: end at the last completed exchange, else before this message,
                # else (one oversized message) at this message alone.
                cut = last_boundary if last_boundary is not None else (i - 1 if i > start else i)
                break
            if completed_after(events, i) or (final and i == n - 1):
                last_boundary = i
                if chars >= soft_chars:
                    cut = i
                    break
        if cut is None:
            if final:
                cut = n - 1
            elif last_boundary is not None and now - events[start].at >= max_age:
                cut = last_boundary  # the oldest message has waited long enough: seal all that is complete
            else:
                break
        cuts.append((start, cut))
        start = cut + 1
    return cuts


def render(events: list[Event]) -> str:
    return "\n\n".join(f"## {e.role} · {e.at.isoformat()}\n{e.content}" for e in events)


async def seal_session(conn, project_id: UUID, session_id: str, *, final: bool, now: datetime) -> dict:
    """Seal whatever is due and queue its extraction. Idempotent: an already sealed message is
    never sealed again, and an episode id names its exact range."""
    session = await (await conn.execute(
        "SELECT agent, identity, label, sealed_through, imported FROM sessions WHERE project_id = %s AND id = %s FOR UPDATE",
        (project_id, session_id))).fetchone()
    if not session:
        return {"skipped": "session gone"}
    rows = await (await conn.execute(
        "SELECT seq, role, content, at FROM session_events WHERE project_id = %s AND session_id = %s AND seq > %s ORDER BY seq",
        (project_id, session_id, session["sealed_through"]))).fetchall()
    events = [Event(r["seq"], r["role"], r["content"], r["at"]) for r in rows]
    cuts = plan_cuts(events, now=now, final=final)
    label = session["label"] or f"{session['agent'] or 'agent'} session {session_id}"
    sealed = []
    for start, end in cuts:
        part = events[start : end + 1]
        first, last = part[0].seq, part[-1].seq
        await ingest_episode(conn, project_id, EpisodeIn(
            id=f"session:{session_id}:{first:05d}-{last:05d}", content=render(part), timestamp=part[0].at,
            identity=session["identity"], label=label,
            metadata={"session_id": session_id, "from_seq": first, "to_seq": last},
            background=session["imported"],
        ))
        sealed.append([first, last])
    if sealed:
        await conn.execute("UPDATE sessions SET sealed_through = %s WHERE project_id = %s AND id = %s",
                           (sealed[-1][1], project_id, session_id))
    return {"sealed": sealed, "pending_messages": len(events) - sum(b - a + 1 for a, b in sealed)}


async def mark_processed(conn, project_id: UUID, metadata: dict) -> None:
    """Advance the session's processed checkpoint after an episode's memory writes (same
    transaction). Ranges are extracted in order within a project, so this only moves forward."""
    session_id, to_seq = metadata.get("session_id"), metadata.get("to_seq")
    if session_id and isinstance(to_seq, int):
        await conn.execute(
            "UPDATE sessions SET processed_through = GREATEST(processed_through, %s) WHERE project_id = %s AND id = %s",
            (to_seq, project_id, session_id))


async def due_sessions(conn, now: datetime) -> list[tuple[UUID, str]]:
    """Live sessions whose oldest unsealed message has waited max_age: candidates for an age seal."""
    cutoff = now - timedelta(minutes=settings.session_max_age_minutes)
    rows = await (await conn.execute(
        """
        SELECT s.project_id, s.id FROM sessions s
        WHERE s.status = 'live' AND s.turns > s.sealed_through
          AND EXISTS (SELECT 1 FROM session_events e WHERE e.project_id = s.project_id AND e.session_id = s.id
                      AND e.seq = s.sealed_through + 1 AND e.at <= %s)
        """, (cutoff,))).fetchall()
    return [(r["project_id"], r["id"]) for r in rows]
