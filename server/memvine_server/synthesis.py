"""Synthesis of one episode: thread pages first, then gated knowledge.

Storing whatever an excerpt states as facts meant, on real sessions, mostly steps of the work and
status snapshots. So the work goes to its thread page (threads.py), edited in place, and only
durable knowledge with a category is kept as memory items; those go through reconciliation,
evidence, staleness anchors and recall like every other memory.
"""
from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from . import code, events
from .checkpoints import mark_processed
from .embeddings import Embedder
from .extraction.extract import extract_details, extract_knowledge
from .extraction.llm import LLM
from .reconcile import DETAIL_MIN_SIMILARITY, judge_batch, reconcile
from .review import has_forgotten, matches_forgotten
from .threads import forget_episode, resolve_thread, update_threads

log = logging.getLogger("memvine.synthesis")

# Conversation mode reads ahead: while one episode is reconciled, the details of the next few queued
# episodes of the project are already being extracted, since extraction reads the excerpt alone and is
# the slow step of a long import. Kept in this process only; a restart just extracts again.
AHEAD = 3
_ahead: dict[tuple[UUID, str, int], asyncio.Task] = {}


async def details_of(conn, llm: LLM, project, ep, profile: str, context: str, instructions: str | None):
    """The episode's details: the extraction started ahead for it when there is one (made without
    <context>), else extracted now. Either way the next queued episodes are started."""
    project_id = project["id"]

    def start(e, ctx: str = "") -> asyncio.Task:
        return asyncio.create_task(extract_details(llm, label=e["label"] or e["id"], occurred_at=e["occurred_at"],
                                                   content=e["content"], project=profile, context=ctx, instructions=instructions))

    task = _ahead.pop((project_id, ep["id"], hash(ep["content"])), None)
    for key in [k for k in _ahead if k[0] == project_id and k[1] == ep["id"]]:  # an older version of this episode
        _ahead.pop(key).cancel()
    queued = await (await conn.execute(
        "SELECT e.id, e.content, e.occurred_at, e.label FROM jobs j JOIN episodes e ON e.project_id = j.project_id AND e.id = j.episode_id "
        "WHERE j.project_id = %s AND j.kind = 'extract' AND j.status = 'queued' ORDER BY j.background, j.id LIMIT %s",
        (project_id, AHEAD))).fetchall()
    for e in queued:
        key = (project_id, e["id"], hash(e["content"]))
        if key not in _ahead and len(_ahead) < 50:
            _ahead[key] = start(e)
            _ahead[key].add_done_callback(lambda t: t.cancelled() or t.exception())  # read later, or never
    if task is not None:
        try:
            return await task
        except Exception as exc:  # noqa: BLE001 - extract it now instead, where a failure belongs to this job
            log.info("extraction ahead failed (%s); extracting now", type(exc).__name__)
    return await start(ep, context)


def project_profile(project) -> str:
    """What extraction judges relevance against: the project's name and description."""
    description = (project.get("description") or "").strip()
    return f"{project['name']} — {description}" if description else project["name"]


async def project_categories(conn, project_id: UUID) -> list[tuple[str, str, str]]:
    """The project's own categories as (slug, name, description), oldest first."""
    rows = await (await conn.execute(
        "SELECT slug, name, description FROM project_categories WHERE project_id = %s ORDER BY created_at, slug",
        (project_id,))).fetchall()
    return [(r["slug"], r["name"], r["description"]) for r in rows]


async def synthesize_episode(conn, llm: LLM, embedder: Embedder, project, ep, context: str) -> dict:
    project_id: UUID = project["id"]
    # The project's memory instructions and categories (migration 0008): they adjust what is kept.
    instructions = (project.get("memory_instructions") or "").strip() or None
    categories = await project_categories(conn, project_id)
    # A retry or re-send rewrites the pages this episode fed; take its old evidence off them first.
    await forget_episode(conn, project_id, ep["id"])
    profile = project_profile(project)
    written, thread_stats = [], {}
    # Conversation mode (migration 0011): every concrete detail is kept and there are no pages, since
    # a conversation is not a piece of work with a state.
    details = project.get("memory_mode") == "conversation"
    if details:
        extraction = await details_of(conn, llm, project, ep, profile, context, instructions)
    else:
        # Pages are project-wide; an episode for a narrower audience feeds only audience-scoped knowledge.
        if ep["audience"] == "project":
            written, thread_stats = await update_threads(conn, llm, embedder, project_id, ep, profile,
                                                          instructions=instructions)
        extraction = await extract_knowledge(llm, label=ep["label"] or ep["id"], occurred_at=ep["occurred_at"], content=ep["content"],
                                             project=profile, threads=[(t["key"], t["title"]) for t in written], context=context,
                                             instructions=instructions, categories=categories)
    relations: dict[str, int] = {}
    code_context = await code.code_for_episode(conn, project_id, ep)
    anchored = 0
    dropped_forgotten = 0
    # A person forgot it once: never learn it again (review.py).
    forgotten = await has_forgotten(conn, project_id) and bool(extraction.items)
    vectors = await embedder.embed([c.statement for c in extraction.items]) if forgotten else []
    # Conversation mode judges the whole excerpt against memory as it was before it (reconcile.judge_batch).
    judged = await judge_batch(conn, llm, embedder, project_id, extraction.items, recorded_at=ep["occurred_at"],
                               audience=ep["audience"], min_similarity=DETAIL_MIN_SIMILARITY) if details else None
    for i, cand in enumerate(extraction.items):
        if forgotten and (match := await matches_forgotten(conn, project_id, cand.statement, vectors[i])):
            dropped_forgotten += 1
            await events.record(conn, project_id, "refused", memory_id=match, actor=events.SYNTHESIS,
                                detail={"reason": "forgotten"}, episode_id=ep["id"])
            continue
        decision = await reconcile(conn, llm, embedder, project_id, cand, recorded_at=ep["occurred_at"], episode_id=ep["id"],
                                   audience=ep["audience"], judgement=judged[i] if judged else None)
        relations[decision.relation] = relations.get(decision.relation, 0) + 1
        thread_id = await resolve_thread(conn, project_id, cand.thread_key, written)
        # Thread-scoped knowledge is shown only beside its page; without a page it is everyone's.
        scope = "thread" if cand.scope == "thread" and thread_id else "project"
        # A restated item ('same') keeps what it had and gains what it lacked.
        filled = await (await conn.execute(
            "WITH before AS (SELECT id, category FROM memory_items WHERE id = %s) "
            "UPDATE memory_items m SET category = COALESCE(m.category, %s), why = COALESCE(m.why, %s), "
            "thread_id = COALESCE(m.thread_id, %s), scope = COALESCE(m.scope, %s) FROM before WHERE m.id = before.id "
            "RETURNING before.category AS was, m.category AS now",
            (decision.memory_id, cand.category, cand.why, thread_id, scope))).fetchone()
        if filled and filled["was"] != filled["now"] and decision.relation == "same":
            await events.record(conn, project_id, "category_changed", memory_id=decision.memory_id, actor=events.SYNTHESIS,
                                detail={"before": filled["was"], "after": filled["now"]}, episode_id=ep["id"])
        anchored += await code.anchor(conn, project_id, decision.memory_id, kind=cand.kind,
                                      text=" ".join([cand.statement, cand.quote, *cand.subjects]), code=code_context,
                                      relation=decision.relation)
    await mark_processed(conn, project_id, ep["metadata"] or {})
    return {
        "threads": [t["key"] for t in written],
        "thread_pages": thread_stats.get("pages", 0),
        "threads_created": thread_stats.get("created", 0),
        "threads_merged": thread_stats.get("merged", 0),
        "thread_items_dropped_no_quote": thread_stats.get("dropped_no_quote", 0),
        "thread_items_expired": thread_stats.get("expired", 0),
        "candidates": len(extraction.items),
        "context_chars": len(context),
        "dropped_no_quote": extraction.dropped_no_quote,
        "dropped_invalid": extraction.dropped_invalid,
        "dropped_gate": extraction.dropped_gate,
        "dropped_forgotten": dropped_forgotten,
        "relations": relations,
        "anchored": anchored,
    }
