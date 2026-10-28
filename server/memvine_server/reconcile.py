"""Decide how a new candidate relates to what the project already knows.

For each candidate we look at active items that share a subject and are close
in embedding space, and ask: same fact (merge evidence), an update (supersede
the old value), a contradiction, an extension (insert, linked to the item it adds
detail to), or something different (insert). The LLM
makes the call except for near-identical statements, which need no model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from . import events
from .embeddings import Embedder
from .extraction.extract import Candidate, normalize, parse_json
from .extraction.llm import LLM, LLMError, fast
from .extraction.prompts import RECONCILE_SYSTEM, reconcile_user
from .store import add_evidence, insert_item

MAX_CANDIDATES = 5
# Loose on purpose: subject overlap already narrows the field and the LLM judges each
# pair, so the floor only has to keep clearly unrelated items from costing a call.
# Tune against real embeddings once we have reconciliation data from a benchmark run.
MIN_SIMILARITY = 0.45
# Subject tags are the extractor's wording and drift ("muse" vs "muse prod database"), so an item
# this similar is a candidate even without a shared subject: on BGE-small, restatements and changed
# values of one fact score about 0.9, different facts about one project 0.6-0.7.
SIMILAR_WITHOUT_SUBJECT = 0.8
IDENTICAL_SIMILARITY = 0.97
# A reworded restatement of one fact (same category, same numbers and dates) this close is the same
# item without asking the model: the existing wording stays and the restatement adds evidence, so
# synthesis does not write near-copies. A changed value (an update) can score this high too, so the
# numbers must match and the new wording must not announce a change; the model judges those.
NEAR_DUPLICATE_SIMILARITY = 0.92
_NUMBERS = re.compile(r"\d+(?:[.:]\d+)*")
_CHANGE = re.compile(r"\b(now|no longer|moved|switched|changed|replaced|instead|renamed|anymore|not)\b")


def near_duplicate(cand: Candidate, old) -> bool:
    if old["similarity"] < NEAR_DUPLICATE_SIMILARITY or old["kind"] != cand.kind:
        return False
    if old.get("category") and cand.category and old["category"] != cand.category:
        return False
    before, after = normalize(old["statement"]), normalize(cand.statement)
    if set(_CHANGE.findall(after)) - set(_CHANGE.findall(before)):
        return False
    return set(_NUMBERS.findall(before)) == set(_NUMBERS.findall(after))


@dataclass
class Decision:
    relation: str  # same | update | contradiction | extends | different | new
    memory_id: UUID
    related_id: UUID | None = None


async def _related(conn, project_id: UUID, cand: Candidate, embedding: list[float], audience: str):
    # Stale items are candidates too: restating one against the new code re-validates it
    # (code.anchor on 'same'), and a changed value supersedes it, instead of duplicating it.
    if cand.subjects:
        where, params = "(subjects && %s OR 1 - (embedding <=> %s::vector) >= %s)", [cand.subjects, embedding, SIMILAR_WITHOUT_SUBJECT]
    else:
        where, params = "TRUE", []
    rows = await (
        await conn.execute(
            f"""
            SELECT id, statement, kind, category, recorded_at, valid_from, confidence,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM memory_items
            WHERE project_id = %s AND audience = %s AND status IN ('active', 'stale') AND embedding IS NOT NULL
              AND (valid_to IS NULL OR valid_to > now()) AND {where}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            [embedding, project_id, audience, *params, embedding, MAX_CANDIDATES],
        )
    ).fetchall()
    return [r for r in rows if r["similarity"] >= MIN_SIMILARITY]


async def _classify(llm: LLM, cand: Candidate, new_date: datetime, related: list) -> tuple[str, dict | None]:
    """One judgement over all related items: which one the candidate matches, and how."""
    for old in related:
        if old["similarity"] >= IDENTICAL_SIMILARITY and normalize(old["statement"]) == normalize(cand.statement):
            return "same", old
    for old in related:
        if near_duplicate(cand, old):
            return "same", old
    try:
        raw = await fast(llm).complete(
            RECONCILE_SYSTEM,
            reconcile_user(cand.statement, new_date.date().isoformat(),
                           [(r["recorded_at"].date().isoformat(), r["statement"]) for r in related]),
            max_tokens=300,
        )
        verdict = parse_json(raw)
        relation = str(verdict.get("relation", "different"))
        match = verdict.get("match")
    except (LLMError, ValueError, AttributeError):
        # When the judge fails, keeping both items is the safe default.
        return "different", None
    if relation not in {"same", "update", "contradiction", "extends"} or not isinstance(match, int) or not 1 <= match <= len(related):
        return "different", None
    return relation, related[match - 1]


async def reconcile(conn, llm: LLM, embedder: Embedder, project_id: UUID, cand: Candidate, *,
                    recorded_at: datetime, episode_id: str, audience: str = "project", actor: str = events.SYNTHESIS) -> Decision:
    """Facts only merge or supersede within one audience: a memory one audience may read is
    never replaced or extended by evidence another audience cannot see. Each outcome is recorded
    in the memory's history (events.py) as `actor`."""
    embedding = (await embedder.embed([cand.statement]))[0]
    related = await _related(conn, project_id, cand, embedding, audience)

    relation, old = await _classify(llm, cand, recorded_at, related) if related else ("different", None)
    if old is not None:
        if relation == "same":
            await add_evidence(conn, project_id, old["id"], episode_id, cand.quote)
            # A preference strengthens with repetition: each restatement raises it one step
            # (low, medium, high), however confidently this one was stated, so "high" means
            # "said three times". Anything else takes a restatement's high confidence at once.
            if old["kind"] == "preference":
                raised = {"low": "medium", "medium": "high"}.get(old["confidence"], old["confidence"])
            else:
                raised = "high" if cand.confidence == "high" else old["confidence"]
            if raised != old["confidence"]:
                await conn.execute("UPDATE memory_items SET confidence = %s WHERE id = %s", (raised, old["id"]))
            await events.record(conn, project_id, "restated", memory_id=old["id"], actor=actor, episode_id=episode_id,
                                detail={"confidence": raised} if raised != old["confidence"] else None)
            return Decision("same", old["id"], old["id"])

        if relation == "contradiction":
            # Neither claim replaces the other: both stay active, linked, and are shown together.
            new_id = await insert_item(conn, project_id, cand, embedding, recorded_at=recorded_at, episode_id=episode_id, audience=audience)
            await conn.execute(
                "UPDATE memory_items SET conflicts_with = array_append(conflicts_with, %s) WHERE id = %s", (old["id"], new_id))
            await conn.execute(
                "UPDATE memory_items SET conflicts_with = array_append(conflicts_with, %s) WHERE id = %s", (new_id, old["id"]))
            await events.record_batch(conn, project_id, [
                _created(new_id, episode_id, relation, old["id"]),
                {"kind": "contradicted", "memory_id": new_id, "detail": {"with": old["id"]}},
                {"kind": "contradicted", "memory_id": old["id"], "detail": {"with": new_id}, "episode_id": episode_id},
            ], actor=actor)
            return Decision("contradiction", new_id, old["id"])

        if relation == "extends":
            # Adds detail without replacing anything: both stay active, the new one linked to the old.
            new_id = await insert_item(conn, project_id, cand, embedding, recorded_at=recorded_at, episode_id=episode_id, audience=audience)
            await conn.execute(
                "INSERT INTO memory_links (project_id, from_id, to_id, kind, episode_id) VALUES (%s, %s, %s, 'extends', %s) "
                "ON CONFLICT DO NOTHING", (project_id, new_id, old["id"], episode_id))
            await events.record_batch(conn, project_id, [
                _created(new_id, episode_id, relation, old["id"]),
                {"kind": "extended", "memory_id": old["id"], "detail": {"by": new_id}, "episode_id": episode_id},
            ], actor=actor)
            return Decision("extends", new_id, old["id"])

        if relation == "update":
            new_id = await insert_item(
                conn, project_id, cand, embedding, recorded_at=recorded_at, episode_id=episode_id, supersedes=old["id"], audience=audience
            )
            boundary = cand.valid_from or recorded_at
            await conn.execute(
                """
                UPDATE memory_items
                SET status = 'superseded', superseded_by = %s, valid_to = COALESCE(valid_to, %s)
                WHERE id = %s
                """,
                (new_id, boundary, old["id"]),
            )
            await events.record_batch(conn, project_id, [
                _created(new_id, episode_id, relation, old["id"]),
                {"kind": "supersedes", "memory_id": new_id, "detail": {"of": old["id"]}},
                {"kind": "superseded", "memory_id": old["id"], "detail": {"by": new_id}, "episode_id": episode_id},
            ], actor=actor)
            return Decision(relation, new_id, old["id"])

    new_id = await insert_item(conn, project_id, cand, embedding, recorded_at=recorded_at, episode_id=episode_id, audience=audience)
    relation = "new" if not related else "different"
    await events.record_batch(conn, project_id, [_created(new_id, episode_id, relation, None)], actor=actor)
    return Decision(relation, new_id, related[0]["id"] if related else None)


def _created(memory_id: UUID, episode_id: str, relation: str, related: UUID | None) -> dict:
    detail = {"relation": relation, **({"related": related} if related is not None else {})}
    return {"kind": "created", "memory_id": memory_id, "detail": detail, "episode_id": episode_id}
