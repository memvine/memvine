"""Sample data: a small, realistic project memory a new user can recall against in their first minute.

Loading writes the sources and their memories directly, the way synthesis would have left them,
with no model call and no charge: each source is an episode marked `metadata.source = "sample"`
(with a finished extraction job, so Sources shows it as synthesized), and each memory is inserted
with its real embedding and evidence in that source. The set includes one superseded pair (the
launch date moved; recall shows the new date with the old one as `previous`) and one contradiction
pair (two people disagree about weekend escalations; both stay active and are shown together).
The memories also hang off threads of work (the launch, release 4.2.0, invoice
parsing, support escalations), and each source names who said it, so the graph shows
`supersedes`, `contradicts` and `decided_in` edges and the people behind them.

Deleting removes the sample sources through the ordinary episode deletion path, which takes their
memories with them. Loading again when the full sample is present changes nothing; a partial sample
(one of its sources deleted by hand) is replaced by a fresh copy.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from .embeddings import Embedder
from .extraction.extract import Candidate
from .ingest import delete_episode, ingest_episode
from .models import EpisodeIn
from .passages import index_episode
from .store import insert_item

SAMPLE_SOURCE = "sample"


@dataclass
class SampleMemory:
    key: str
    statement: str
    kind: str
    category: str
    subjects: list[str]
    quote: str
    why: str | None = None
    standing: bool = False
    supersedes: str | None = None    # key of an earlier sample memory this one replaces
    conflicts_with: str | None = None  # key of an earlier sample memory this one contradicts
    # A preference said once starts low and strengthens each time it is restated.
    confidence: str = "high"
    thread: str | None = None  # key of a SAMPLE_THREADS entry


@dataclass
class SampleSource:
    id: str
    days_ago: int
    label: str
    turns: list[tuple[str, str]]  # (speaker, text)
    memories: list[SampleMemory] = field(default_factory=list)

    @property
    def speaker(self) -> str:
        """Who said it: the source's first speaker, recorded as the episode's identity."""
        return self.turns[0][0]


# Threads of work the sample's knowledge belongs to: key -> (kind, title, status, summary).
SAMPLE_THREADS: dict[str, tuple[str, str, str, str]] = {
    "topic:public-launch": ("topic", "Public launch", "open",
                            "The public launch date and what moved it: legal pushed back on the refund copy."),
    "release:4.2.0": ("release", "Release 4.2.0", "open",
                      "Scope of release 4.2.0 and how release work is merged."),
    "topic:invoice-parsing": ("topic", "Invoice parsing", "done",
                              "How invoice fields the prompt misses are mapped; InvoiceFieldAliases was kept, then dropped."),
    "topic:support-escalations": ("topic", "Support escalations", "open",
                                  "Where escalations go and what happens on weekends; Sam and Dana disagree."),
}


def _sources(now: datetime) -> list[SampleSource]:
    """The sample, oldest source first. Dates are relative to now so the story is always current."""
    day = lambda d: (now + timedelta(days=d)).strftime("%-d %b")  # noqa: E731
    launch_old, launch_new = day(4), day(18)
    return [
        SampleSource(
            "sample:kickoff", 21, "Sample · kickoff with Priya",
            [("Priya", f"Target public launch is {launch_old}. Refunds over $500 always need a human approver; the support "
                       "bot may only draft them. And never deploy on Fridays. I prefer async review over meetings."),
             ("Agent", f"Noted: launch on {launch_old}, refunds above $500 go to a human approver, no Friday deploys, "
                       "and reviews happen async.")],
            [SampleMemory("launch_old", f"The public launch is planned for {launch_old}.", "semantic", "decision", ["launch"],
                          f"Target public launch is {launch_old}.", thread="topic:public-launch"),
             SampleMemory("refunds", "Refunds over $500 always need a human approver; the support bot may only draft them.",
                          "procedural", "rule", ["refunds"], "Refunds over $500 always need a human approver",
                          why="Large refunds carry fraud and chargeback risk."),
             SampleMemory("fridays", "Never deploy on Fridays.", "procedural", "rule", ["deploys"], "never deploy on Fridays",
                          standing=True),
             SampleMemory("async", "Priya prefers async review over meetings.", "preference", "preference", ["priya"],
                          "I prefer async review over meetings.", confidence="low")],
        ),
        SampleSource(
            "sample:invoice-parsing-1", 17, "Sample · invoice parsing",
            [("Priya", "The prompt misses some invoice fields. Keep the InvoiceFieldAliases class as a fallback for now."),
             ("Agent", "Kept InvoiceFieldAliases; it maps field names the prompt does not recognize.")],
            [SampleMemory("synonyms_old", "Keep the InvoiceFieldAliases class as a fallback for invoice fields the prompt misses.",
                          "semantic", "decision", ["invoice parsing"], "Keep the InvoiceFieldAliases class as a fallback for now.",
                          thread="topic:invoice-parsing")],
        ),
        SampleSource(
            "sample:launch-update", 12, "Sample · launch update",
            [("Priya", f"Legal pushed back on the refund copy, so we're moving the launch from {launch_old} to {launch_new}."),
             ("Agent", f"Updated the release checklist to {launch_new} and flagged the pricing page copy as blocked on legal.")],
            [SampleMemory("launch_new", f"The public launch moved to {launch_new} because legal pushed back on the refund copy.",
                          "semantic", "decision", ["launch"], f"we're moving the launch from {launch_old} to {launch_new}",
                          why="Legal pushed back on the refund copy.", supersedes="launch_old",
                          thread="topic:public-launch")],
        ),
        SampleSource(
            "sample:release-4.2.0", 6, "Sample · release/4.2.0",
            [("Priya", "Scope for 4.2.0: PDF invoice export (#429), multi-currency totals (#430 and #437) and payment "
                       "reminder emails (#434). Multi-language invoices wait for 4.2.1. Release work never pushes to main; "
                       "only I merge release branches."),
             ("Agent", "Understood. I'll work on release/4.2.0 and feature branches and open pull requests for you to merge.")],
            [SampleMemory("release", "Release 4.2.0 ships PDF invoice export (#429), multi-currency totals (#430, #437) and "
                                     "payment reminder emails (#434); multi-language invoices wait for 4.2.1.",
                          "semantic", "decision", ["release 4.2.0"], "Scope for 4.2.0: PDF invoice export (#429)",
                          thread="release:4.2.0"),
             SampleMemory("merges", "Only Priya merges release work; agents open pull requests and never push release "
                                    "branches to main.", "procedural", "rule", ["releases", "priya"],
                          "Release work never pushes to main; only I merge release branches.", thread="release:4.2.0")],
        ),
        SampleSource(
            "sample:invoice-parsing-2", 5, "Sample · invoice parsing",
            [("Priya", "Drop the InvoiceFieldAliases class. The prompt already maps 'bill to' to 'customer name' and the "
                       "table keeps drifting from it. Rely on the prompt."),
             ("Agent", "Removed InvoiceFieldAliases and its tests in #442; field aliases now live only in the invoice prompt.")],
            [SampleMemory("synonyms_new", "Drop InvoiceFieldAliases (done in #442): invoice field aliases live only in the LLM "
                                          "prompt, because the alias table kept drifting from it.",
                          "semantic", "decision", ["invoice parsing"], "Drop the InvoiceFieldAliases class.",
                          why="The alias table kept drifting from the prompt.", supersedes="synonyms_old",
                          thread="topic:invoice-parsing")],
        ),
        SampleSource(
            "sample:escalations", 3, "Sample · support escalations",
            [("Sam", "Escalations should go to the #support-tier2 Slack channel, not email: tier 2 watches Slack and "
                     "email sat unread. On weekends they wait in the channel until Monday; nobody gets paged."),
             ("Agent", "Switched escalation routing to #support-tier2.")],
            [SampleMemory("escalations", "Escalations go to the #support-tier2 Slack channel, not email.", "procedural", "rule",
                          ["escalations"], "Escalations should go to the #support-tier2 Slack channel, not email",
                          why="Tier 2 watches Slack; email sat unread.", thread="topic:support-escalations"),
             SampleMemory("weekend_wait", "Weekend escalations wait in #support-tier2 until Monday; nobody is paged.",
                          "procedural", "rule", ["escalations"], "On weekends they wait in the channel until Monday",
                          thread="topic:support-escalations")],
        ),
        SampleSource(
            "sample:on-call", 1, "Sample · on-call review",
            [("Dana", "Weekend escalations page the on-call engineer right away. A customer waited two days last month."),
             ("Agent", "Noted.")],
            [SampleMemory("weekend_page", "Weekend escalations page the on-call engineer right away.", "procedural", "rule",
                          ["escalations"], "Weekend escalations page the on-call engineer right away.",
                          conflicts_with="weekend_wait", thread="topic:support-escalations")],
        ),
    ]


def sample_size() -> tuple[int, int]:
    """(sources, memories) in the sample."""
    sources = _sources(datetime.now(UTC))
    return len(sources), sum(len(s.memories) for s in sources)


async def sample_episode_ids(conn, project_id: UUID) -> list[str]:
    rows = await (await conn.execute(
        "SELECT id FROM episodes WHERE project_id = %s AND metadata->>'source' = %s ORDER BY id", (project_id, SAMPLE_SOURCE),
    )).fetchall()
    return [r["id"] for r in rows]


async def delete_sample(conn, project_id: UUID) -> dict:
    """Delete every sample source and the memories drawn from them."""
    ids = await sample_episode_ids(conn, project_id)
    before = await _memory_count(conn, project_id)
    for episode_id in ids:
        await delete_episode(conn, project_id, episode_id)
    # The sample's speakers go with it, unless another source names them or someone gave them traits.
    speakers = sorted({s.speaker for s in _sources(datetime.now(UTC))})
    await conn.execute(
        "DELETE FROM identities i WHERE i.project_id = %s AND i.identity = ANY(%s) AND i.origin = 'ingest' AND i.traits = '{}' "
        "AND NOT EXISTS (SELECT 1 FROM episodes e WHERE e.project_id = i.project_id AND e.identity = i.identity)",
        (project_id, speakers))
    return {"sources": len(ids), "memories": before - await _memory_count(conn, project_id)}


async def _memory_count(conn, project_id: UUID) -> int:
    return (await (await conn.execute("SELECT count(*) AS n FROM memory_items WHERE project_id = %s", (project_id,))).fetchone())["n"]


async def load_sample(conn, embedder: Embedder, project_id: UUID) -> dict:
    """Write the sample into a project. Returns {"loaded": bool, "sources": n, "memories": n}."""
    now = datetime.now(UTC).replace(hour=10, minute=0, second=0, microsecond=0)
    sources = _sources(now)
    present = set(await sample_episode_ids(conn, project_id))
    if present == {s.id for s in sources}:
        return {"loaded": False, "sources": len(sources), "memories": sum(len(s.memories) for s in sources)}
    if present:  # a partial sample: start over rather than patch it
        await delete_sample(conn, project_id)

    statements = [m.statement for s in sources for m in s.memories]
    vectors = dict(zip(statements, await embedder.embed(statements), strict=True))
    ids: dict[str, UUID] = {}
    threads: dict[str, UUID] = {}
    for src in sources:
        at = now - timedelta(days=src.days_ago)  # every source is at least a day old, so never in the future
        content = "\n\n".join(
            f"## {who} · {(at + timedelta(minutes=i)).strftime('%Y-%m-%dT%H:%M')}\n{text}" for i, (who, text) in enumerate(src.turns))
        await ingest_episode(conn, project_id, EpisodeIn(
            id=src.id, content=content, timestamp=at, identity=src.speaker, label=src.label,
            metadata={"source": SAMPLE_SOURCE}))
        for mem in src.memories:
            cand = Candidate(statement=mem.statement, kind=mem.kind, subjects=mem.subjects, assertion="stated",
                             confidence=mem.confidence, valid_from=at, quote=mem.quote, standing=mem.standing,
                             category=mem.category, why=mem.why)
            old = ids.get(mem.supersedes) if mem.supersedes else None
            new_id = await insert_item(conn, project_id, cand, vectors[mem.statement], recorded_at=at, episode_id=src.id,
                                       supersedes=old)
            ids[mem.key] = new_id
            thread_id = await _sample_thread(conn, embedder, project_id, mem.thread, src.id, at, threads) if mem.thread else None
            await conn.execute("UPDATE memory_items SET scope = 'project', thread_id = %s WHERE id = %s", (thread_id, new_id))
            if old:
                await conn.execute(
                    "UPDATE memory_items SET status = 'superseded', superseded_by = %s, valid_to = %s WHERE id = %s",
                    (new_id, at, old))
            if mem.conflicts_with:
                other = ids[mem.conflicts_with]
                await conn.execute("UPDATE memory_items SET conflicts_with = array_append(conflicts_with, %s) WHERE id = %s",
                                   (other, new_id))
                await conn.execute("UPDATE memory_items SET conflicts_with = array_append(conflicts_with, %s) WHERE id = %s",
                                   (new_id, other))
        # Nothing to synthesize: the extraction job is recorded as done, so Sources shows the
        # source as synthesized with its memories, and the worker never picks it up.
        relations = {"new": sum(1 for m in src.memories if not m.supersedes and not m.conflicts_with)}
        if any(m.supersedes for m in src.memories):
            relations["update"] = sum(1 for m in src.memories if m.supersedes)
        if any(m.conflicts_with for m in src.memories):
            relations["contradiction"] = sum(1 for m in src.memories if m.conflicts_with)
        await conn.execute(
            "UPDATE jobs SET status = 'done', finished_at = now(), result = %s "
            "WHERE project_id = %s AND episode_id = %s AND kind = 'extract' AND status = 'queued'",
            (json.dumps({"sample": True, "candidates": len(src.memories), "relations": relations}), project_id, src.id))
        await index_episode(conn, embedder, project_id, src.id)
    return {"loaded": True, "sources": len(sources), "memories": len(statements)}


async def _sample_thread(conn, embedder: Embedder, project_id: UUID, key: str, episode_id: str, at: datetime,
                         threads: dict[str, UUID]) -> UUID:
    """The sample thread `key`, created on first use; each source that feeds it becomes one of its
    sources, so deleting the sample's sources deletes its threads."""
    if key not in threads:
        kind, title, status, summary = SAMPLE_THREADS[key]
        embedding = (await embedder.embed([f"{title}. {summary}"]))[0]
        threads[key] = (await (await conn.execute(
            """
            INSERT INTO threads (project_id, kind, key, title, status, summary, embedding, first_seen_at, last_activity_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (project_id, key) DO UPDATE SET last_activity_at = GREATEST(threads.last_activity_at, EXCLUDED.last_activity_at)
            RETURNING id
            """, (project_id, kind, key, title, status, summary, embedding, at, at))).fetchone())["id"]
    await conn.execute("UPDATE threads SET last_activity_at = GREATEST(last_activity_at, %s) WHERE id = %s", (at, threads[key]))
    await conn.execute("INSERT INTO thread_sources (thread_id, project_id, episode_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                       (threads[key], project_id, episode_id))
    return threads[key]
