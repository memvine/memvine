"""Slots: the profile of a person, the memory of a project in conversation mode.

Coding-mode reconciliation finds what a new item relates to by similarity and asks a model about the
pair (reconcile.py). In a conversation that fails: every line is about the same person, a changed
value resembles a dozen others, and the model retired the wrong one. Here each memory is the value
of a named slot ("constraint/monthly-book-budget"). The extractor reads the whole profile, which is
small, and names the slot of what it keeps; placing the item is then a lookup:

- no such slot: a new memory;
- the slot holds the same statement: evidence is added, nothing is written twice;
- "update": the slot's value is replaced, and the old one stays as history in the order said;
- "conflict": both stay current, linked, and recall shows them together.

Only the extractor's word retires a value, and only for a slot it was shown: a slot name used again
for another thing gets a name of its own. What is written, and its history, is reconcile's.

Net new. A profile fills with lines that say nearly the same under different slot names ("likes
Sanderson's short stories" as a preference, then as a habit). So an item about to get a slot of its
own is first compared with the lines it most resembles (`net_new`, the fast model): already covered,
it only adds evidence to the line that covers it; adding something lasting to a line, it is folded
into that line; otherwise it is new. The check can drop or fold, never replace: a different value
for the same thing is "new" there, and a folded line must keep every number of both. Standing
instructions take no part in it, on either side.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from . import events
from .embeddings import Embedder
from .extraction.extract import SLOT_CHARS, Candidate, normalize, parse_json
from .extraction.llm import LLM, fast
from .extraction.prompts import NET_NEW_SYSTEM, net_new_user
from .reconcile import _NUMBERS, IDENTICAL_SIMILARITY, Decision, Judgement, near_duplicate, reconcile, same_values
from .store import add_evidence

# The extractor is given this many lines at most, the newest first. A profile is meant to hold a few
# dozen; one that outgrows this is no longer read whole, and the oldest slots stop being matched.
PROFILE_LINES = 200
# Current at the moment an excerpt was said, not at the moment it is read: a conversation imported
# later, or rebuilt, is placed against the plans that were still ahead then.
CURRENT = "slot IS NOT NULL AND status IN ('active', 'stale') AND (valid_to IS NULL OR valid_to > %s)"
# The net-new check: the lines at least this similar to the item, at most this many. On BGE-small two
# lines about one subject of a person's life score 0.6 and up, lines about different subjects of the
# same person about 0.5.
NET_NEW_MIN_SIMILARITY = 0.6
NET_NEW_LINES = 5
MERGED_WORDS = 60
# A standing instruction is never compared, covered or folded: it is served with every answer from a
# section of its own, and folded into a line about the person it stops being an instruction. On a
# first trial "always verify subtitle availability" was folded into a goal about bilingual films and
# left the standing section.
INSTRUCTION = "instruction/"


async def profile(conn, project_id: UUID, audience: str, at: datetime) -> list[tuple[str, str, str]]:
    """What memory held about the person at `at`, as (slot, statement, the day it was said), by slot."""
    rows = await (await conn.execute(
        f"SELECT slot, statement, recorded_at FROM memory_items WHERE project_id = %s AND audience = %s AND {CURRENT} "
        "ORDER BY created_at DESC, id LIMIT %s", (project_id, audience, at, PROFILE_LINES))).fetchall()
    return sorted((r["slot"], r["statement"], r["recorded_at"].date().isoformat()) for r in rows)


async def net_new(llm: LLM, cand: Candidate, near: list) -> tuple[str, dict | None, str | None]:
    """What `cand` adds to the lines it most resembles: ("covered", line, None), ("adds", line, the
    line rewritten to say both) or ("new", None, None). Anything the model gets wrong is "new":
    keeping both is the safe default."""
    try:
        verdict = parse_json(await fast(llm).complete(NET_NEW_SYSTEM, net_new_user(cand.statement, [r["statement"] for r in near]),
                                                      max_tokens=300))
        kind, match = str(verdict.get("verdict")), verdict.get("match")
        merged = " ".join(str(verdict.get("merged") or "").split())
    except Exception:  # noqa: BLE001 - a failed check never costs the item
        return "new", None, None
    if kind not in ("covered", "adds") or not isinstance(match, int) or not 1 <= match <= len(near):
        return "new", None, None
    old = near[match - 1]
    if kind == "covered":
        return "covered", old, None
    numbers = set(_NUMBERS.findall(normalize(old["statement"]))) | set(_NUMBERS.findall(normalize(cand.statement)))
    if not merged or len(merged.split()) > MERGED_WORDS or not numbers <= set(_NUMBERS.findall(normalize(merged))):
        return "new", None, None
    return "adds", old, merged


def says_the_same(cand: Candidate, old) -> bool:
    return (old["similarity"] >= IDENTICAL_SIMILARITY and normalize(old["statement"]) == normalize(cand.statement)) \
        or near_duplicate(cand, old)


def free_slot(slot: str, taken: set[str]) -> str:
    n = 2
    while f"{slot[:SLOT_CHARS - 4]}-{n}" in taken:
        n += 1
    return f"{slot[:SLOT_CHARS - 4]}-{n}"


async def place(conn, llm: LLM | None, embedder: Embedder, project_id: UUID, cand: Candidate, *, recorded_at: datetime,
                episode_id: str, audience: str = "project", actor: str = events.SYNTHESIS) -> Decision:
    """Store `cand` as the value of its slot. Within one audience, as every reconciliation is.
    `llm`: the model for the net-new check; without one an item with no slot of its own is new."""
    embedding = (await embedder.embed([cand.statement]))[0]
    held = await (await conn.execute(
        "SELECT id, slot, statement, kind, category, recorded_at, valid_from, confidence, "
        "1 - (embedding <=> %s::vector) AS similarity "
        f"FROM memory_items WHERE project_id = %s AND audience = %s AND embedding IS NOT NULL AND {CURRENT} "
        "ORDER BY created_at DESC, id", (embedding, project_id, audience, recorded_at))).fetchall()
    in_slot = [r for r in held if r["slot"] == cand.slot]
    # The same statement under any slot is the same memory: the model may name a slot twice over.
    twin = next((r for r in sorted(held, key=lambda r: -r["similarity"]) if says_the_same(cand, r)), None)
    relation, old = "different", None
    if twin is not None:
        relation, old = "same", twin
    elif in_slot and cand.relation == "conflict":
        relation, old = "contradiction", in_slot[0]
    elif in_slot and cand.relation == "update":
        relation, old = "update", in_slot[0]
    elif in_slot and cand.relation == "same" and same_values(in_slot[0]["statement"], cand.statement):
        relation, old = "same", in_slot[0]
    elif in_slot and cand.relation == "same":
        # "Same" with another number or an announced change is a new value, whatever the model called it.
        relation, old = "update", in_slot[0]
    elif in_slot:
        # Called new, under a name already taken: another thing. It never replaces what the slot holds.
        cand.slot = free_slot(cand.slot, {r["slot"] for r in held})
    if old is None and llm is not None and not cand.slot.startswith(INSTRUCTION):
        # About to get a slot of its own: does it add anything to the lines it most resembles?
        near = [r for r in sorted(held, key=lambda r: -r["similarity"])
                if r["similarity"] >= NET_NEW_MIN_SIMILARITY and not r["slot"].startswith(INSTRUCTION)][:NET_NEW_LINES]
        verdict, line, merged = await net_new(llm, cand, near) if near else ("new", None, None)
        if verdict == "covered":
            relation, old, in_slot = "same", line, [line]
        elif verdict == "adds":
            # Folded into the line it adds to: one memory says both, backed by both sources.
            vector = (await embedder.embed([merged]))[0]
            await conn.execute("UPDATE memory_items SET statement = %s, embedding = %s WHERE id = %s", (merged, vector, line["id"]))
            await add_evidence(conn, project_id, line["id"], episode_id, cand.quote)
            await events.record(conn, project_id, "restated", memory_id=line["id"], actor=actor, episode_id=episode_id,
                                detail={"folded": True})
            return Decision("extends", line["id"], line["id"])
    decision = await reconcile(conn, None, embedder, project_id, cand, recorded_at=recorded_at, episode_id=episode_id,
                               audience=audience, actor=actor,
                               judgement=Judgement(embedding, in_slot if old is not None else [], relation, old))
    if decision.relation == "update":
        # A slot that held two conflicting statements: the new value settles both.
        others = [r["id"] for r in in_slot if r["id"] != old["id"]]
        if others:
            await conn.execute(
                "UPDATE memory_items SET status = 'superseded', superseded_by = %s, valid_to = COALESCE(valid_to, %s) "
                "WHERE id = ANY(%s) AND status IN ('active', 'stale')", (decision.memory_id, recorded_at, others))
            await events.record_batch(conn, project_id, [
                {"kind": "superseded", "memory_id": other, "detail": {"by": decision.memory_id}, "episode_id": episode_id}
                for other in others], actor=actor)
    return decision
