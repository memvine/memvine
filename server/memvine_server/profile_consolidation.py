"""Consolidation of a conversation project's profile: what one line at a time cannot do.

The profile of a person (slots.py) is written an excerpt at a time. Each item is checked against the
lines it most resembles before it is written, but that check may only drop or fold one item into one
line. So a long conversation still leaves the same thing spread over several lines: a habit said five
ways, thirteen choices of what to read next, the members of a set the person is building up, each in
a line of its own. And nothing in the conversation can be counted: "how many films have I planned
across all the marathons" is a question no search answers, because no passage holds the count.

A run (consolidation.consolidate, for a project in conversation mode) reads the whole profile at
once, with the main model, and does three things:

- Groups. Lines about the same thing become one line: a habit or limit said in pieces; a choice made
  again and again, with the current one first and the earlier ones after it; a set built up over
  time as a list with its count (slot "list/<name>"). The lines of a group are retired as merged
  into the new one, which takes all their evidence, exactly as a coding-mode merge does, so a
  person can undo any of them (consolidation.unmerge).
- Past plans. A plan whose day has passed and that carried no end date stops being current.
- Nothing else. A standing instruction, a line in a recorded conflict, and a memory a person kept or
  wrote are never shown to the model, so they cannot be grouped away.

What the model writes is checked without a model: a group needs two lines of the profile, a slot of
a known type, a statement within GROUP_WORDS words, and, unless it is a list (whose count is new by
nature), no number that none of its lines has (the day a line was said counts as its own). A group
that fails is left as it was.

"Today" is the day of the last thing said in the project, not the day the run happens: a
conversation imported or rebuilt later is tidied as of its own end. A run asks the model nothing
when the profile is the one the last run saw (stats.fingerprint).
"""
from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from uuid import UUID

from . import events
from .embeddings import Embedder
from .extraction.extract import SLOT_TYPES, Candidate, normalize, parse_json, slot_key
from .extraction.llm import LLM, LLMError, LLMRateLimited
from .extraction.prompts import CONSOLIDATE_PROFILE_SYSTEM, consolidate_profile_user
from .reconcile import _NUMBERS
from .slots import INSTRUCTION, free_slot
from .store import insert_item

log = logging.getLogger("memvine.consolidation")

ACTOR = "consolidation"
MIN_LINES = 8          # a smaller profile has nothing spread out yet
MAX_LINES = 200        # one call reads the whole profile; a larger one is read newest first
GROUP_WORDS = 90       # the prompt asks for 70; a list a little over is still a list
LIST = "list/"


def _protected(m) -> bool:
    return m["review"] == "kept" or m["origin"] == "person"


def fingerprint(lines: list) -> str:
    return hashlib.sha256("\n".join(sorted(str(r["id"]) for r in lines)).encode()).hexdigest()[:16]


async def tidy_profile(conn, llm: LLM, embedder: Embedder, project_id: UUID) -> dict:
    """One run over a conversation project's profile, in the caller's transaction. Returns its stats."""
    stats = {"merged": 0, "groups": 0, "lists": 0, "expired_plans": 0, "refused_groups": 0, "linked": 0, "flagged": 0}
    last = await (await conn.execute("SELECT max(occurred_at) AS at FROM episodes WHERE project_id = %s", (project_id,))).fetchone()
    today = (last and last["at"]) or datetime.now(UTC)
    rows = await (await conn.execute(
        "SELECT m.id, m.slot, m.statement, m.kind, m.category, m.subjects, m.audience, m.review, m.origin, m.recorded_at, "
        "m.valid_to, m.conflicts_with FROM memory_items m WHERE m.project_id = %s AND m.slot IS NOT NULL AND m.status = 'active' "
        "AND m.forgotten_at IS NULL AND m.flag IS NULL AND (m.valid_to IS NULL OR m.valid_to > %s) "
        "ORDER BY m.created_at DESC, m.id LIMIT %s", (project_id, today, MAX_LINES * 2))).fetchall()
    taken = {r["slot"] for r in rows}
    stats["active"] = len(rows)
    seen: list[str] = []
    for audience in sorted({r["audience"] for r in rows}):  # a merge never crosses audiences
        lines = [r for r in rows if r["audience"] == audience and not r["slot"].startswith(INSTRUCTION)
                 and not r["conflicts_with"] and not _protected(r)][:MAX_LINES]
        lines.sort(key=lambda r: (r["slot"], r["recorded_at"]))
        if len(lines) < MIN_LINES:
            continue
        seen.append(fingerprint(lines))
        previous = await (await conn.execute(
            "SELECT stats FROM consolidation_runs WHERE project_id = %s AND finished_at IS NOT NULL AND error IS NULL "
            "AND stats ? 'fingerprint' ORDER BY id DESC LIMIT 1", (project_id,))).fetchone()
        if previous and seen[-1] in str(previous["stats"].get("fingerprint", "")).split(","):
            stats["unchanged"] = stats.get("unchanged", 0) + 1
            continue
        verdict = await _ask(llm, lines, today, stats)
        if verdict is None:
            seen.pop()  # unread: the next run asks again
            continue
        used: set[int] = set()
        for group in verdict.get("groups") if isinstance(verdict.get("groups"), list) else []:
            members = _members(group, lines, used)
            if members is None or not await _merge(conn, embedder, project_id, audience, members, group, taken, stats):
                stats["refused_groups"] += 1
                continue
            used |= {lines.index(m) + 1 for m in members}
        past = [lines[n - 1] for n in verdict.get("past") or [] if isinstance(n, int) and not isinstance(n, bool)
                and 1 <= n <= len(lines) and n not in used and lines[n - 1]["slot"].startswith("plan/")]
        if past:
            done = await conn.execute(
                "UPDATE memory_items SET valid_to = %s WHERE project_id = %s AND id = ANY(%s) AND valid_to IS NULL",
                (today, project_id, [r["id"] for r in past]))
            stats["expired_plans"] += done.rowcount
    # The profile as this run left it is what the next run compares with: a run that changed it is
    # followed by one more, which then finds it settled.
    if seen and not (stats["merged"] or stats["expired_plans"]):
        stats["fingerprint"] = ",".join(seen)
    return stats


async def _ask(llm: LLM, lines: list, today: datetime, stats: dict) -> dict | None:
    user = consolidate_profile_user(today.date().isoformat(),
                                    [(r["slot"], r["recorded_at"].date().isoformat(), r["statement"]) for r in lines])
    try:
        parsed = parse_json(await llm.complete(CONSOLIDATE_PROFILE_SYSTEM, user, max_tokens=6000))
    except LLMRateLimited:
        raise
    except (LLMError, ValueError) as exc:
        log.warning("profile consolidation unreadable: %s", type(exc).__name__)
        stats["judge_errors"] = stats.get("judge_errors", 0) + 1
        return None
    return parsed if isinstance(parsed, dict) else None


def _members(group, lines: list, used: set[int]) -> list | None:
    """The lines a group names, when it names at least two that no earlier group took."""
    numbers = group.get("lines") if isinstance(group, dict) else None
    if not isinstance(numbers, list):
        return None
    picked = sorted({n for n in numbers if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= len(lines)})
    if len(picked) < 2 or len(picked) != len(numbers) or used & set(picked):
        return None
    return [lines[n - 1] for n in picked]


def acceptable(slot: str | None, statement: str, members: list) -> bool:
    """A group's statement, checked without a model."""
    if slot is None or slot.startswith(INSTRUCTION) or not statement or len(statement.split()) > GROUP_WORDS:
        return False
    if slot.startswith(LIST):
        return True  # its count is a number none of its lines has
    held = set().union(*(set(_NUMBERS.findall(normalize(m["statement"]))) for m in members))
    # The day a line was said is shown to the model beside it, and "since 2 April 2024" is no invention.
    for said in (m.get("recorded_at") for m in members):
        if said is not None:
            held |= {str(said.year), str(said.month), f"{said.month:02d}", str(said.day), f"{said.day:02d}"}
    return set(_NUMBERS.findall(normalize(statement))) <= held


async def _merge(conn, embedder: Embedder, project_id: UUID, audience: str, members: list, group: dict,
                 taken: set[str], stats: dict) -> bool:
    slot, statement = slot_key(group.get("slot")), " ".join(str(group.get("statement") or "").split())
    if not acceptable(slot, statement, members):
        return False
    ids = [m["id"] for m in members]
    source = await (await conn.execute(
        "SELECT episode_id, quote FROM evidence WHERE memory_id = ANY(%s) ORDER BY episode_id LIMIT 1", (ids,))).fetchone()
    if not source:
        return False
    if slot in taken and slot not in {m["slot"] for m in members}:
        slot = free_slot(slot, taken)  # the name belongs to a line outside the group
    category, kind = SLOT_TYPES[slot.split("/")[0]]
    newest = max(members, key=lambda m: m["recorded_at"])
    subjects = list(dict.fromkeys(s for m in members for s in (m["subjects"] or [])))[:3]
    cand = Candidate(statement=statement, kind=kind, subjects=subjects, assertion="stated", confidence="high",
                     valid_from=None, quote=source["quote"], category=category, scope="project", slot=slot)
    vector = (await embedder.embed([statement]))[0]
    new_id = await insert_item(conn, project_id, cand, vector, recorded_at=newest["recorded_at"],
                               episode_id=source["episode_id"], audience=audience)
    await events.record(conn, project_id, "created", memory_id=new_id, actor=ACTOR, episode_id=source["episode_id"],
                        detail={"relation": "merged"})
    batch = []
    for m in members:
        added = await (await conn.execute(
            "INSERT INTO evidence (memory_id, project_id, episode_id, quote) "
            "SELECT %s, project_id, episode_id, quote FROM evidence WHERE memory_id = %s ON CONFLICT DO NOTHING "
            "RETURNING episode_id, md5(quote) AS digest", (new_id, m["id"]))).fetchall()
        batch += [
            {"kind": "merged", "memory_id": m["id"], "detail": {"into": new_id, "judged": "profile"}},
            # Which evidence rows came from this line, so un-merging it takes exactly those off again.
            {"kind": "merged", "memory_id": new_id, "detail": {"from": m["id"], "judged": "profile",
                                                               "evidence": [[r["episode_id"], r["digest"]] for r in added]}},
        ]
    await conn.execute(
        "UPDATE memory_items SET status = 'retired', retired_reason = 'merged', merged_into = %s WHERE project_id = %s AND id = ANY(%s)",
        (new_id, project_id, ids))
    await events.record_batch(conn, project_id, batch, actor=ACTOR)
    taken.add(slot)
    stats["merged"] += len(members)
    stats["groups"] += 1
    stats["lists"] += slot.startswith(LIST)
    return True
