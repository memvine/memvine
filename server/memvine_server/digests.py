"""Digests: in conversation mode, a short account of each excerpt, for questions that ask for a summary.

A detail answers a question about one value. A question like "summarize how the project went" is
about everything, and resembles no single detail: the points a good answer needs rank hundreds deep
in any search. A digest names every topic of its excerpt in about forty words. Every GROUP
consecutive digests are rolled up into one of the level above, and so on, so a conversation of any
length has a cover that fits a briefing: `relevant` starts from the coarsest cover and opens the
roll-ups closest to the question into the digests under them while the budget lasts. Recall serves
the result, in time order, to summary questions only (recall.py).

Written by the fast model when the excerpt is synthesized; `memvine-server backfill-digests` writes
the ones missing from a store built before digests existed. A digest that fails is skipped: the
details are the memory, the digest is a view of the source.
"""
from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from .embeddings import Embedder
from .extraction import prompts
from .extraction.llm import LLM, fast

log = logging.getLogger("memvine.digests")
GROUP = 4            # digests of one level under each roll-up
MIN_CHARS = 40       # shorter than this is a refusal or "NONE", not a digest
MAX_CHARS = 900      # a digest that ran long is cut at a sentence
RETRY_SECONDS = 2.0


def _clean(text: str) -> str | None:
    text = " ".join(text.split())
    if len(text) < MIN_CHARS or text.startswith(("[", "{")):
        return None
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rsplit(". ", 1)[0] + "."
    return text


async def _ask(llm: LLM, system: str, user: str, what: str) -> str | None:
    for attempt in (1, 2):
        try:
            return _clean(await fast(llm).complete(system, user, max_tokens=400))
        except Exception as exc:  # noqa: BLE001 - a digest is never worth failing the episode for
            log.warning("digest of %s failed (attempt %d): %s: %s", what, attempt, type(exc).__name__, str(exc)[:200])
            await asyncio.sleep(RETRY_SECONDS)
    return None


async def compose(llm: LLM, ep) -> str | None:
    """The digest of one episode, or None when there is nothing to say or the model failed."""
    return await _ask(llm, prompts.DIGEST_SYSTEM, prompts.digest_user(ep["occurred_at"].date().isoformat(), ep["content"]),
                      f"episode {ep['id']}")


async def store(conn, embedder: Embedder, project_id: UUID, ep, text: str | None) -> bool:
    """Replace the episode's digest with `text` (None removes it). The roll-ups above the old one go with it."""
    await conn.execute("DELETE FROM digests WHERE project_id = %s AND episode_id = %s AND level = 1", (project_id, ep["id"]))
    if not text:
        return False
    vector = (await embedder.embed([text]))[0]
    await conn.execute(
        "INSERT INTO digests (project_id, level, episode_id, content, starts_at, occurred_at, audience, embedding) "
        "VALUES (%s, 1, %s, %s, %s, %s, %s, %s)",
        (project_id, ep["id"], text, ep["occurred_at"], ep["occurred_at"], ep.get("audience") or "project", vector))
    return True


async def roll_up(conn, llm: LLM, embedder: Embedder, project_id: UUID, *, at_once: int = 1) -> int:
    """Roll every full group of parentless digests up one level, oldest first, level by level.
    A group short of GROUP waits for the digests still to come. Returns how many roll-ups were written."""
    written, level = 0, 1
    slots = asyncio.Semaphore(max(1, at_once))

    async def one(group) -> str | None:
        async with slots:
            return await _ask(llm, prompts.ROLLUP_SYSTEM,
                              prompts.rollup_user([(d["occurred_at"].date().isoformat(), d["content"]) for d in group]),
                              f"{len(group)} digests")

    while True:
        rows = await (await conn.execute(
            "SELECT id, content, starts_at, occurred_at, audience FROM digests WHERE project_id = %s AND level = %s "
            "AND parent_id IS NULL ORDER BY audience, occurred_at, id", (project_id, level))).fetchall()
        by_audience: dict[str, list] = {}
        for r in rows:
            by_audience.setdefault(r["audience"], []).append(r)
        groups = [members[i:i + GROUP] for members in by_audience.values() for i in range(0, len(members) - GROUP + 1, GROUP)]
        if not groups:
            if not rows:
                return written
            level += 1
            continue

        texts = await asyncio.gather(*(one(g) for g in groups))
        made = 0
        for group, text in zip(groups, texts, strict=True):
            if not text:
                continue
            vector = (await embedder.embed([text]))[0]
            parent = await (await conn.execute(
                "INSERT INTO digests (project_id, level, content, starts_at, occurred_at, audience, embedding) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (project_id, level + 1, text, group[0]["starts_at"], group[-1]["occurred_at"], group[0]["audience"], vector))).fetchone()
            await conn.execute("UPDATE digests SET parent_id = %s WHERE id = ANY(%s)", (parent["id"], [d["id"] for d in group]))
            made += 1
        written += made
        if not made:  # the model failed on every group: try again another time
            return written
        level += 1


async def backfill(conn, llm: LLM, embedder: Embedder, project_id: UUID, *, at_once: int = 4) -> tuple[int, int]:
    """Write the digests a conversation-mode project lacks, then roll them up. Returns (digests, roll-ups) written."""
    rows = await (await conn.execute(
        "SELECT e.id, e.content, e.occurred_at, e.audience FROM episodes e WHERE e.project_id = %s AND e.expired_at IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM digests d WHERE d.project_id = e.project_id AND d.episode_id = e.id AND d.level = 1) "
        "ORDER BY e.occurred_at, e.id", (project_id,))).fetchall()
    slots = asyncio.Semaphore(max(1, at_once))

    async def one(ep) -> str | None:
        async with slots:
            return await compose(llm, ep)

    written = 0
    for start in range(0, len(rows), 24):  # stored batch by batch, so an interrupted run keeps what it wrote
        batch = rows[start:start + 24]
        texts = await asyncio.gather(*(one(ep) for ep in batch))
        for ep, text in zip(batch, texts, strict=True):
            written += await store(conn, embedder, project_id, ep, text)
        await conn.commit()
    rolled = await roll_up(conn, llm, embedder, project_id, at_once=at_once)
    await conn.commit()
    return written, rolled


async def relevant(conn, project_id: UUID, embedding: list[float], *, audiences: list[str] | None, as_of, budget: int,
                   tokens_of) -> list[dict]:
    """A cover of the conversation up to `as_of` within `budget` tokens, in time order.

    It starts as the coarsest cover: the top roll-ups and whatever is not rolled up yet. Then, closest
    to the question first, a roll-up is opened into the digests under it for as long as they fit. If
    even the coarsest cover is too large, the digests furthest from the question are left out."""
    clauses, params = ["project_id = %s"], [project_id]
    if audiences is not None:
        clauses.append("audience = ANY(%s)")
        params.append(audiences)
    if as_of:
        # A roll-up that runs past the moment asked about is not used; the digests under it are.
        clauses.append("starts_at <= %s")
        params.append(as_of)
    rows = await (await conn.execute(
        f"SELECT id, level, parent_id, episode_id, content, starts_at, occurred_at, "
        f"coalesce(1 - (embedding <=> %s::vector), 0)::float AS similarity FROM digests WHERE {' AND '.join(clauses)}",
        [embedding, *params])).fetchall()
    if as_of:
        rows = [r for r in rows if r["occurred_at"] <= as_of]
    known = {r["id"] for r in rows}
    children: dict[int, list[dict]] = {}
    for r in rows:
        if r["parent_id"] in known:
            children.setdefault(r["parent_id"], []).append(r)
    cost = lambda r: tokens_of(r["content"]) + 6  # noqa: E731
    cover = {r["id"]: r for r in rows if r["parent_id"] not in known}
    spent = sum(cost(r) for r in cover.values())
    for r in sorted(cover.values(), key=lambda r: r["similarity"]):  # too large even so: furthest from the question go
        if spent <= budget:
            break
        spent -= cost(cover.pop(r["id"]))
    closed: set[int] = set()
    while True:
        openable = [r for r in cover.values() if r["id"] in children and r["id"] not in closed]
        if not openable:
            break
        best = max(openable, key=lambda r: r["similarity"])
        extra = sum(cost(c) for c in children[best["id"]]) - cost(best)
        if spent + extra > budget:
            closed.add(best["id"])
            continue
        spent += extra
        del cover[best["id"]]
        cover.update({c["id"]: c for c in children[best["id"]]})
    return sorted(cover.values(), key=lambda r: (r["starts_at"], r["occurred_at"], r["id"]))
