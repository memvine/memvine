"""Passages: an episode's text cut into retrieval-sized pieces, embedded and indexed.

Facts are dense but lossy; passages keep the original wording. Recall, when asked (include.passages), uses them to fill the
budget that facts and pages leave, preferring passages from the episodes the chosen facts
came from.
"""
from __future__ import annotations

import re
from uuid import UUID

from .embeddings import Embedder

PASSAGE_CHARS = 1_500
_TURN = re.compile(r"(?m)^(?=## )")


def split_passages(content: str, size: int = PASSAGE_CHARS, *, pack: bool = True) -> list[str]:
    """Turns (blocks starting '## ') packed up to `size` characters; a longer turn is split
    on sentence boundaries. Text with no turn headers is treated as one turn.

    `pack=False` (conversation mode) keeps every turn a passage of its own. In a person's long
    conversation with an assistant the person's turns are short and the assistant's long: packed
    together, a turn such as "my budget is now $50" is found only as the tail of a page of advice,
    and a briefing held about six of the person's turns where one passage per turn holds thirty."""
    turns = [t.strip() for t in _TURN.split(content) if t.strip()]
    pieces: list[str] = []
    for turn in turns:
        if len(turn) <= size:
            pieces.append(turn)
            continue
        header, _, body = turn.partition("\n") if turn.startswith("## ") else ("", "", turn)
        current = header
        for sentence in re.split(r"(?<=[.!?])\s+", body):
            if current and len(current) + len(sentence) + 1 > size:
                pieces.append(current.strip())
                current = f"{header} (cont.)" if header else ""
            current = f"{current}\n{sentence}" if current else sentence
        if current.strip():
            pieces.append(current.strip())
    if not pack:
        return pieces
    out: list[str] = []
    for piece in pieces:  # pack small neighbours together
        if out and len(out[-1]) + len(piece) + 2 <= size:
            out[-1] = f"{out[-1]}\n\n{piece}"
        else:
            out.append(piece)
    return out


async def index_episode(conn, embedder: Embedder, project_id: UUID, episode_id: str) -> int:
    project = await (await conn.execute("SELECT memory_mode FROM projects WHERE id = %s", (project_id,))).fetchone()
    per_turn = bool(project) and project["memory_mode"] == "conversation"
    ep = await (
        await conn.execute(
            "SELECT content, occurred_at, audience FROM episodes WHERE project_id = %s AND id = %s", (project_id, episode_id)
        )
    ).fetchone()
    await conn.execute("DELETE FROM passages WHERE project_id = %s AND episode_id = %s", (project_id, episode_id))
    if not ep:
        return 0
    pieces = split_passages(ep["content"], pack=not per_turn)
    vectors = await embedder.embed(pieces)
    for ord_, (text, vector) in enumerate(zip(pieces, vectors, strict=True)):
        await conn.execute(
            "INSERT INTO passages (project_id, episode_id, ord, content, occurred_at, audience, embedding) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (project_id, episode_id, ord_, text, ep["occurred_at"], ep["audience"], vector),
        )
    return len(pieces)
