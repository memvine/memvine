"""Synthesis of one episode: thread pages first, then gated knowledge.

Storing whatever an excerpt states as facts meant, on real sessions, mostly steps of the work and
status snapshots. So the work goes to its thread page (threads.py), edited in place, and only
durable knowledge with a category is kept as memory items; those go through reconciliation,
evidence, staleness anchors and recall like every other memory.
"""
from __future__ import annotations

from uuid import UUID

from . import code
from .checkpoints import mark_processed
from .embeddings import Embedder
from .extraction.extract import extract_knowledge
from .extraction.llm import LLM
from .reconcile import reconcile
from .review import has_forgotten, matches_forgotten
from .threads import forget_episode, resolve_thread, update_threads


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
    for i, cand in enumerate(extraction.items):
        if forgotten and await matches_forgotten(conn, project_id, cand.statement, vectors[i]):
            dropped_forgotten += 1
            continue
        decision = await reconcile(conn, llm, embedder, project_id, cand, recorded_at=ep["occurred_at"], episode_id=ep["id"],
                                   audience=ep["audience"])
        relations[decision.relation] = relations.get(decision.relation, 0) + 1
        thread_id = await resolve_thread(conn, project_id, cand.thread_key, written)
        # Thread-scoped knowledge is shown only beside its page; without a page it is everyone's.
        scope = "thread" if cand.scope == "thread" and thread_id else "project"
        # A restated item ('same') keeps what it had and gains what it lacked.
        await conn.execute(
            "UPDATE memory_items SET category = COALESCE(category, %s), why = COALESCE(why, %s), thread_id = COALESCE(thread_id, %s), "
            "scope = COALESCE(scope, %s) WHERE id = %s", (cand.category, cand.why, thread_id, scope, decision.memory_id))
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
