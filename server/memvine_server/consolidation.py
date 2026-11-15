"""Background consolidation: a conservative, periodic tidy-up of one project's memory that catches
what write-time reconciliation (reconcile.py) missed. Nothing is deleted; every action is reversible
and recorded in memory_events with actor 'consolidation'.

A run (worker job kind 'consolidate', one per project at a time, never for a paused project, see
`schedule_due`) does three bounded steps over current memories (active, not expired, not forgotten,
not already flagged):

a. Duplicates. Pairs of the same audience, kind, category and scope with cosine >= DUPLICATE_SIMILARITY.
   At >= NEAR_DUPLICATE_SIMILARITY with the same numbers and dates and no change words
   (reconcile.same_values) they merge without a model call; otherwise the fast model judges them.
   A merge keeps the stronger item (kept by a person, standing, higher confidence, then older), copies
   the other's evidence onto it, and retires the other with retired_reason 'merged' and merged_into.
b. Missed supersedes. Pairs of the same audience with cosine >= PAIR_SIMILARITY that share a subject
   (or are >= DUPLICATE_SIMILARITY alike) go to the same batched judgement: 'update' links them exactly
   as reconcile's update path does (superseded / supersedes), 'contradiction' links conflicts_with.
c. Re-check the bar. Synthesis-made memories nobody kept that are a week old and never recalled, and
   every such fact (or item in a project's own category), are re-asked the three tests (CLAUDE.md) with
   their evidence quote and the project's memory instructions. A failure flags 'no_longer_passes'.

Doubt never acts: 'unsure' flags 'possible_duplicate' or 'possible_outdated' for a person (the
console's Review card), and so does any action that would change a memory a person kept or made.
Steps a and b share one model call per run (at most MAX_JUDGED_PAIRS pairs), step c one more (at most
MAX_BAR_ITEMS items). Every verdict is stored in consolidation_judgements, so a second run asks
nothing about what the first one judged and changes nothing.

A project in conversation mode is consolidated differently (profile_consolidation.py): its profile is
read whole by the main model, lines about the same thing become one line or a list with its count,
and a plan whose day has passed stops being current. None of the three steps above runs there.

Undo: forgetting the newer item of a supersede restores the older (store.retire); PATCH status active
on a merged-away item un-merges it (`unmerge`): it is current again and the evidence the merge copied
onto the kept item is taken off it again.
"""
from __future__ import annotations

import json
import logging
from uuid import UUID

from . import events
from .embeddings import Embedder
from .extraction.extract import parse_json
from .extraction.llm import LLM, LLMError, LLMRateLimited, fast
from .extraction.prompts import (
    CONSOLIDATE_BAR_SYSTEM,
    CONSOLIDATE_PAIRS_SYSTEM,
    consolidate_bar_user,
    consolidate_pairs_user,
)
from .reconcile import NEAR_DUPLICATE_SIMILARITY, SIMILAR_WITHOUT_SUBJECT, same_values

log = logging.getLogger("memvine.consolidation")

ACTOR = "consolidation"
JOB_KIND = "consolidate"
# Thresholds (cosine, BGE-small): restatements of one fact score about 0.9, different facts about one
# project 0.6-0.7 (reconcile.py). Merge candidates need 0.88; at 0.92 with the same values they merge
# without asking; a missed update or contradiction is looked for from 0.8 (reconcile's own bar for
# relating items without a shared subject).
DUPLICATE_SIMILARITY = 0.88
PAIR_SIMILARITY = SIMILAR_WITHOUT_SUBJECT  # 0.8
NEIGHBOURS = 5               # nearest items looked at per memory
MAX_RULE_MERGES = 50         # merges without the model, per run
MAX_JUDGED_PAIRS = 50        # pairs in the one batched model call, per run
MAX_BAR_ITEMS = 30           # items re-checked against the three tests, per run
BAR_AGE_DAYS = 7             # an unkept memory never recalled this long is re-checked
# Scheduling (schedule_due).
MIN_ACTIVE = 10              # smaller projects are not consolidated
DUE_NEW_MEMORIES = 20        # a run is due after this many new memories since the last one ...
DUE_HOURS = 24               # ... or a day
BUILTIN_CATEGORIES = ("decision", "rule", "gotcha", "fact", "preference")
CONFIDENCE = {"low": 0, "medium": 1, "high": 2}

# Current and untouched: what consolidation may act on.
ELIGIBLE = ("m.status = 'active' AND (m.valid_to IS NULL OR m.valid_to > now()) AND m.forgotten_at IS NULL "
            "AND m.flag IS NULL")
ITEM_COLUMNS = ("m.id, m.statement, m.kind, m.category, coalesce(m.scope, 'project') AS scope, m.audience, m.subjects, "
                "m.standing, m.confidence, m.review, m.origin, m.recorded_at, m.valid_from, m.created_at, m.supersedes, "
                "m.conflicts_with")


# ------------------------------------------------------------------ scheduling


async def schedule_due(conn) -> list[UUID]:
    """Queue a consolidation for every project that is due: not paused, at least MIN_ACTIVE active
    memories, no consolidation queued or running (or failed in the last day), and never consolidated,
    consolidated over DUE_HOURS ago, or DUE_NEW_MEMORIES memories added since. Returns the projects queued."""
    rows = await (await conn.execute(
        """
        INSERT INTO jobs (project_id, kind, episode_id, background)
        SELECT p.id, %s, 'consolidation', true FROM projects p
        WHERE NOT p.processing_paused
          AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.project_id = p.id AND j.kind = %s
                          AND (j.status IN ('queued', 'running')
                               OR (j.status = 'failed' AND j.finished_at > now() - make_interval(hours => %s))))
          AND (SELECT count(*) FROM memory_items m WHERE m.project_id = p.id AND m.status = 'active') >= %s
          AND (p.consolidated_at IS NULL OR p.consolidated_at < now() - make_interval(hours => %s)
               OR (SELECT count(*) FROM memory_items m WHERE m.project_id = p.id AND m.created_at > p.consolidated_at) >= %s)
        ON CONFLICT (project_id) WHERE kind = 'consolidate' AND status IN ('queued', 'running') DO NOTHING
        RETURNING project_id
        """, (JOB_KIND, JOB_KIND, DUE_HOURS, MIN_ACTIVE, DUE_HOURS, DUE_NEW_MEMORIES))).fetchall()
    return [r["project_id"] for r in rows]


async def queue(conn, project_id: UUID) -> tuple[bool, int]:
    """Queue a run now (POST /v1/projects/{slug}/consolidate). (False, id) when one is already waiting or running."""
    row = await (await conn.execute(
        "SELECT id FROM jobs WHERE project_id = %s AND kind = %s AND status IN ('queued', 'running') ORDER BY id LIMIT 1",
        (project_id, JOB_KIND))).fetchone()
    if row:
        return False, row["id"]
    row = await (await conn.execute(
        "INSERT INTO jobs (project_id, kind, episode_id, background) VALUES (%s, %s, 'consolidation', true) "
        "ON CONFLICT (project_id) WHERE kind = 'consolidate' AND status IN ('queued', 'running') DO NOTHING RETURNING id",
        (project_id, JOB_KIND))).fetchone()
    if not row:  # another caller queued one in between
        return await queue(conn, project_id)
    return True, row["id"]


async def run_job(conn, llm: LLM, project_id: UUID, embedder: Embedder | None = None) -> dict:
    """The worker's entry point: one recorded run in its own transaction. A failure rolls the run's
    changes back, is recorded on the run, and is raised for the job to retry. `embedder`: needed to
    write the merged lines of a conversation project's profile."""
    run = await (await conn.execute(
        "INSERT INTO consolidation_runs (project_id) VALUES (%s) RETURNING id", (project_id,))).fetchone()
    try:
        async with conn.transaction():
            stats = await consolidate(conn, llm, project_id, embedder)
            await conn.execute("UPDATE projects SET consolidated_at = now() WHERE id = %s", (project_id,))
            await conn.execute("UPDATE consolidation_runs SET finished_at = now(), stats = %s WHERE id = %s",
                               (_jsonb(stats), run["id"]))
    except BaseException as exc:
        await conn.execute("UPDATE consolidation_runs SET finished_at = now(), error = %s WHERE id = %s",
                           (f"{type(exc).__name__}: {exc}"[:500], run["id"]))
        raise
    return {"run": run["id"], **stats}


def _jsonb(value) -> str:
    return json.dumps(value, default=str)


async def runs(conn, project_id: UUID, limit: int = 20) -> list[dict]:
    return list(await (await conn.execute(
        "SELECT id, started_at, finished_at, stats, error FROM consolidation_runs WHERE project_id = %s "
        "ORDER BY started_at DESC, id DESC LIMIT %s", (project_id, limit))).fetchall())


# ------------------------------------------------------------------ the run


class _Run:
    def __init__(self, conn, llm: LLM, project_id: UUID) -> None:
        self.conn, self.llm, self.project_id = conn, llm, project_id
        self.stats = {"merged": 0, "superseded": 0, "contradictions": 0, "flagged": 0,
                      "judged_pairs": 0, "checked_items": 0, "flags": {}}
        self.done: set[UUID] = set()       # merged away, superseded or flagged in this run: no second action
        self.absorbed: set[UUID] = set()   # kept a merge in this run: never merged away in the same run


async def consolidate(conn, llm: LLM, project_id: UUID, embedder: Embedder | None = None) -> dict:
    """One run over a project (in the caller's transaction). Returns its stats; `linked` counts
    supersedes and contradictions, `flagged` every flag set.

    A conversation project gets the profile pass instead (profile_consolidation.py). The steps below
    judge pairs by similarity and let the fast model retire the older of two: among the lines of one
    person's profile that is how the wrong value was once replaced, so there only the extractor's
    word for a slot it was shown retires a value, and consolidation groups and lists."""
    mode = await (await conn.execute("SELECT memory_mode FROM projects WHERE id = %s", (project_id,))).fetchone()
    if mode and mode["memory_mode"] == "conversation":
        if embedder is None:
            return {"skipped": "no embedder for the profile pass", "merged": 0, "linked": 0, "flagged": 0}
        from .profile_consolidation import tidy_profile  # imports slots, which this module's users may not need
        return await tidy_profile(conn, llm, embedder, project_id)
    active = (await (await conn.execute(
        f"SELECT count(*) AS n FROM memory_items m WHERE m.project_id = %s AND {ELIGIBLE}", (project_id,))).fetchone())["n"]
    if active < MIN_ACTIVE:
        return {"skipped": f"fewer than {MIN_ACTIVE} active memories", "active": active,
                "merged": 0, "linked": 0, "flagged": 0}
    run = _Run(conn, llm, project_id)
    await _pairs(run)
    await _bar(run)
    s = run.stats
    return {**s, "linked": s["superseded"] + s["contradictions"], "active": active}


# ------------------------------------------------------------------ a and b: pairs


async def _candidate_pairs(run: _Run) -> list[dict]:
    """Pairs of current items of one audience at least PAIR_SIMILARITY alike, not judged before and not
    already linked, most alike first: {"older", "newer", "similarity", "duplicate"}."""
    rows = await (await run.conn.execute(
        f"""
        WITH live AS (SELECT m.id, m.embedding, m.audience FROM memory_items m
                      WHERE m.project_id = %s AND {ELIGIBLE} AND m.embedding IS NOT NULL)
        SELECT a.id AS a, n.id AS b, n.similarity FROM live a
        CROSS JOIN LATERAL (
            SELECT b.id, 1 - (b.embedding <=> a.embedding) AS similarity FROM live b
            WHERE b.audience = a.audience AND b.id <> a.id
            ORDER BY b.embedding <=> a.embedding LIMIT %s) n
        WHERE n.similarity >= %s
        """, (run.project_id, NEIGHBOURS, PAIR_SIMILARITY))).fetchall()
    best: dict[tuple[UUID, UUID], float] = {}
    for r in rows:
        key = tuple(sorted((r["a"], r["b"]), key=str))
        best[key] = max(best.get(key, 0.0), float(r["similarity"]))
    if not best:
        return []
    ids = list({i for pair in best for i in pair})
    items = {r["id"]: r for r in await (await run.conn.execute(
        f"SELECT {ITEM_COLUMNS} FROM memory_items m WHERE m.project_id = %s AND m.id = ANY(%s)",
        (run.project_id, ids))).fetchall()}
    judged = {(r["memory_id"], r["other_id"]) for r in await (await run.conn.execute(
        "SELECT memory_id, other_id FROM consolidation_judgements WHERE project_id = %s AND step = 'pair' "
        "AND (memory_id = ANY(%s) OR other_id = ANY(%s))", (run.project_id, ids, ids))).fetchall()}
    linked = {(r["from_id"], r["to_id"]) for r in await (await run.conn.execute(
        "SELECT from_id, to_id FROM memory_links WHERE project_id = %s AND (from_id = ANY(%s) OR to_id = ANY(%s))",
        (run.project_id, ids, ids))).fetchall()}
    out = []
    for (x, y), similarity in best.items():
        a, b = items.get(x), items.get(y)
        if not a or not b:
            continue
        older, newer = sorted((a, b), key=lambda m: (m["recorded_at"], m["created_at"], str(m["id"])))
        if ((older["id"], newer["id"]) in judged or (newer["id"], older["id"]) in judged
                or (older["id"], newer["id"]) in linked or (newer["id"], older["id"]) in linked
                or newer["id"] in (older["conflicts_with"] or []) or older["id"] in (newer["conflicts_with"] or [])):
            continue
        duplicate = (similarity >= DUPLICATE_SIMILARITY and older["kind"] == newer["kind"]
                     and older["category"] == newer["category"] and older["scope"] == newer["scope"])
        shares_subject = bool(set(older["subjects"] or []) & set(newer["subjects"] or []))
        if not duplicate and not shares_subject and similarity < DUPLICATE_SIMILARITY:
            continue
        out.append({"older": older, "newer": newer, "similarity": round(similarity, 4), "duplicate": duplicate})
    out.sort(key=lambda p: -p["similarity"])
    return out


async def _pairs(run: _Run) -> None:
    pairs = await _candidate_pairs(run)
    to_judge, rule_merges = [], 0
    for p in pairs:
        if (p["duplicate"] and p["similarity"] >= NEAR_DUPLICATE_SIMILARITY
                and same_values(p["older"]["statement"], p["newer"]["statement"])):
            if rule_merges < MAX_RULE_MERGES and _free(run, p):  # past the bound: the next run
                rule_merges += 1
                await _merge_or_flag(run, p, "rule")
        elif len(to_judge) < MAX_JUDGED_PAIRS:
            to_judge.append(p)
    to_judge = [p for p in to_judge if _free(run, p)]
    if not to_judge:
        return
    verdicts = await _judge_pairs(run, to_judge)
    run.stats["judged_pairs"] += len(verdicts)
    for i, p in enumerate(to_judge, 1):
        relation = verdicts.get(i)
        if relation is None:
            continue  # unanswered: asked again next run
        if not _free(run, p):
            continue
        older, newer = p["older"], p["newer"]
        if relation == "same":
            if p["duplicate"]:
                await _merge_or_flag(run, p, "model")
            else:
                await _flag(run, newer, "possible_duplicate", "May say the same as another memory of a different kind or category.", older["id"])
                await _judged(run, p, "flagged_duplicate")
        elif relation == "update":
            await _supersede_or_flag(run, p)
        elif relation == "contradiction":
            await _contradiction(run, p)
        elif relation == "unsure":
            if p["duplicate"]:
                await _flag(run, newer, "possible_duplicate", "May say the same as another memory; the check was unsure.", older["id"])
            else:
                await _flag(run, older, "possible_outdated", "A newer memory may have replaced it; the check was unsure.", newer["id"])
            await _judged(run, p, "unsure")
        else:
            await _judged(run, p, "different")


def _free(run: _Run, p: dict) -> bool:
    return p["older"]["id"] not in run.done and p["newer"]["id"] not in run.done


async def _judge_pairs(run: _Run, pairs: list[dict]) -> dict[int, str]:
    """{pair number: relation} from one fast-model call; {} when the answer cannot be read."""
    user = consolidate_pairs_user([(p["older"]["recorded_at"].date().isoformat(), p["older"]["statement"],
                                    p["newer"]["recorded_at"].date().isoformat(), p["newer"]["statement"]) for p in pairs])
    try:
        raw = await fast(run.llm).complete(CONSOLIDATE_PAIRS_SYSTEM, user, max_tokens=60 * len(pairs) + 200)
        parsed = parse_json(raw)
    except LLMRateLimited:
        raise
    except (LLMError, ValueError) as exc:
        log.warning("consolidation pair judgement unreadable: %s", type(exc).__name__)
        run.stats["judge_errors"] = run.stats.get("judge_errors", 0) + 1
        return {}
    entries = parsed.get("pairs") if isinstance(parsed, dict) else parsed
    out = {}
    for e in entries if isinstance(entries, list) else []:
        if isinstance(e, dict) and isinstance(e.get("pair"), int) and 1 <= e["pair"] <= len(pairs) \
                and e.get("relation") in ("same", "update", "contradiction", "different", "unsure"):
            out[e["pair"]] = e["relation"]
    return out


def _protected(m) -> bool:
    """A memory a person kept or made: consolidation never changes its status, only flags."""
    return m["review"] == "kept" or m["origin"] == "person"


def _survivor(p: dict):
    """(kept, merged away) for a duplicate pair, or None when no merge is allowed: the one to retire was
    kept or made by a person, or it is a standing rule and the other is not."""
    older, newer = p["older"], p["newer"]
    kept = max((older, newer), key=lambda m: (_protected(m), m["standing"], CONFIDENCE.get(m["confidence"], 1), m is older))
    away = newer if kept is older else older
    if _protected(away) or (away["standing"] and not kept["standing"]):
        return None
    return kept, away


async def _merge_or_flag(run: _Run, p: dict, how: str) -> None:
    sides = _survivor(p)
    if sides is None or sides[1]["id"] in run.absorbed:
        if sides is None:
            await _flag(run, p["newer"], "possible_duplicate", "Says the same as another memory a person kept; keep one, forget the other.",
                        p["older"]["id"])
            await _judged(run, p, "flagged_duplicate")
        return  # one that absorbed a merge this run is left for the next run
    kept, away = sides
    added = await (await run.conn.execute(
        "INSERT INTO evidence (memory_id, project_id, episode_id, quote) "
        "SELECT %s, project_id, episode_id, quote FROM evidence WHERE memory_id = %s ON CONFLICT DO NOTHING "
        "RETURNING episode_id, md5(quote) AS digest", (kept["id"], away["id"]))).fetchall()
    await run.conn.execute(
        "UPDATE memory_items SET status = 'retired', retired_reason = 'merged', merged_into = %s WHERE project_id = %s AND id = %s",
        (kept["id"], run.project_id, away["id"]))
    await events.record_batch(run.conn, run.project_id, [
        {"kind": "merged", "memory_id": away["id"], "detail": {"into": kept["id"], "similarity": p["similarity"], "judged": how}},
        # Which evidence rows the merge copied (episode id and an md5 of the quote, never its text), so an
        # un-merge can take exactly those off again.
        {"kind": "merged", "memory_id": kept["id"], "detail": {"from": away["id"], "similarity": p["similarity"], "judged": how,
                                                               "evidence": [[r["episode_id"], r["digest"]] for r in added]}},
    ], actor=ACTOR)
    await _judged(run, p, "merged")
    run.done.add(away["id"])
    run.absorbed.add(kept["id"])
    run.stats["merged"] += 1


async def _supersede_or_flag(run: _Run, p: dict) -> None:
    """The newer replaces the older, linked as reconcile's update path links them. A person's memory, or a
    newer item that already replaces another, is flagged instead."""
    older, newer = p["older"], p["newer"]
    if _protected(older) or newer["supersedes"] is not None:
        await _flag(run, older, "possible_outdated", "A newer memory seems to replace it.", newer["id"])
        await _judged(run, p, "flagged_outdated")
        return
    await run.conn.execute(
        "UPDATE memory_items SET status = 'superseded', superseded_by = %s, valid_to = COALESCE(valid_to, %s) "
        "WHERE project_id = %s AND id = %s", (newer["id"], newer["valid_from"], run.project_id, older["id"]))
    await run.conn.execute("UPDATE memory_items SET supersedes = %s WHERE project_id = %s AND id = %s",
                           (older["id"], run.project_id, newer["id"]))
    await events.record_batch(run.conn, run.project_id, [
        {"kind": "supersedes", "memory_id": newer["id"], "detail": {"of": older["id"]}},
        {"kind": "superseded", "memory_id": older["id"], "detail": {"by": newer["id"]}},
    ], actor=ACTOR)
    await _judged(run, p, "superseded")
    run.done.add(older["id"])
    run.stats["superseded"] += 1


async def _contradiction(run: _Run, p: dict) -> None:
    """Both stay current, linked as reconcile links a contradiction, and are shown together."""
    older, newer = p["older"], p["newer"]
    for a, b in ((older["id"], newer["id"]), (newer["id"], older["id"])):
        await run.conn.execute(
            "UPDATE memory_items SET conflicts_with = array_append(conflicts_with, %s) "
            "WHERE project_id = %s AND id = %s AND NOT (%s = ANY(conflicts_with))", (b, run.project_id, a, b))
    await events.record_batch(run.conn, run.project_id, [
        {"kind": "contradicted", "memory_id": newer["id"], "detail": {"with": older["id"]}},
        {"kind": "contradicted", "memory_id": older["id"], "detail": {"with": newer["id"]}},
    ], actor=ACTOR)
    await _judged(run, p, "contradiction")
    run.stats["contradictions"] += 1


async def _judged(run: _Run, p: dict, verdict: str) -> None:
    await run.conn.execute(
        "INSERT INTO consolidation_judgements (project_id, memory_id, other_id, step, verdict) VALUES (%s, %s, %s, 'pair', %s) "
        "ON CONFLICT (memory_id, other_id, step) DO UPDATE SET verdict = EXCLUDED.verdict, at = now()",
        (run.project_id, p["older"]["id"], p["newer"]["id"], verdict))


async def _flag(run: _Run, m, flag: str, reason: str, related: UUID | None) -> None:
    row = await (await run.conn.execute(
        "UPDATE memory_items SET flag = %s, flag_reason = %s, flag_related = %s WHERE project_id = %s AND id = %s "
        "AND flag IS NULL RETURNING id", (flag, reason[:300], related, run.project_id, m["id"]))).fetchone()
    if not row:
        return
    await events.record(run.conn, run.project_id, "flagged", memory_id=m["id"], actor=ACTOR,
                        detail={"flag": flag, **({"related": related} if related else {})})
    run.done.add(m["id"])
    run.stats["flagged"] += 1
    run.stats["flags"][flag] = run.stats["flags"].get(flag, 0) + 1


# ------------------------------------------------------------------ c: the bar


BAR_TESTS = {"another task", "not in the code", "still true later", "project instructions"}


def _as_int(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    return int(v) if isinstance(v, str) and v.strip().isdigit() else None


def _as_bool(v) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "false"):
        return v.strip().lower() == "true"
    return None


async def _bar(run: _Run) -> None:
    builtins = list(BUILTIN_CATEGORIES)
    rows = await (await run.conn.execute(
        f"""
        SELECT m.id, m.statement, m.category, m.why,
               (SELECT v.quote FROM evidence v WHERE v.memory_id = m.id ORDER BY v.episode_id LIMIT 1) AS quote
        FROM memory_items m
        WHERE m.project_id = %s AND {ELIGIBLE} AND m.origin = 'synthesis' AND m.review = 'new'
          AND (m.category = 'fact' OR NOT (m.category = ANY(%s))
               OR (m.created_at < now() - make_interval(days => %s) AND m.last_recalled_at IS NULL))
          AND NOT EXISTS (SELECT 1 FROM consolidation_judgements j WHERE j.memory_id = m.id AND j.other_id = m.id AND j.step = 'bar')
        ORDER BY m.created_at, m.id LIMIT %s
        """, (run.project_id, builtins, BAR_AGE_DAYS, MAX_BAR_ITEMS))).fetchall()
    rows = [r for r in rows if r["id"] not in run.done]
    if not rows:
        return
    project = await (await run.conn.execute("SELECT memory_instructions FROM projects WHERE id = %s",
                                            (run.project_id,))).fetchone()
    user = consolidate_bar_user([(r["statement"], r["category"], r["why"], r["quote"]) for r in rows],
                                (project or {}).get("memory_instructions"))
    try:
        raw = await fast(run.llm).complete(CONSOLIDATE_BAR_SYSTEM, user, max_tokens=80 * len(rows) + 200)
        parsed = parse_json(raw)
    except LLMRateLimited:
        raise
    except (LLMError, ValueError) as exc:
        log.warning("consolidation bar check unreadable: %s", type(exc).__name__)
        run.stats["judge_errors"] = run.stats.get("judge_errors", 0) + 1
        return
    entries = parsed.get("items") if isinstance(parsed, dict) else parsed
    for e in entries if isinstance(entries, list) else []:
        item, passes = (_as_int(e.get("item")), _as_bool(e.get("passes"))) if isinstance(e, dict) else (None, None)
        if item is None or passes is None or not 1 <= item <= len(rows):
            run.stats["judge_skipped"] = run.stats.get("judge_skipped", 0) + 1
            continue
        m = rows[item - 1]
        run.stats["checked_items"] += 1
        fails = str(e.get("fails") or "").strip()
        # A failing verdict must name the test that fails; without one it is not a reason to flag.
        e["passes"] = passes or fails.lower() not in BAR_TESTS
        if not e["passes"]:
            reason = str(e.get("reason") or "").strip() or "Seems to fail the bar for memory."
            await _flag(run, m, "no_longer_passes", f"{fails[:1].upper()}{fails[1:]}: {reason}" if fails else reason, None)
        await run.conn.execute(
            "INSERT INTO consolidation_judgements (project_id, memory_id, other_id, step, verdict) VALUES (%s, %s, %s, 'bar', %s) "
            "ON CONFLICT (memory_id, other_id, step) DO NOTHING",
            (run.project_id, m["id"], m["id"], "passes" if e["passes"] else "fails"))


# ------------------------------------------------------------------ a person's undo


async def unmerge(conn, project_id: UUID, memory_id: UUID, *, actor: str) -> bool:
    """A merged-away memory is current again (PATCH status active). The evidence the merge copied onto
    the kept memory is taken off it; the kept memory stays as it is otherwise. The pair stays judged,
    so consolidation does not merge them again. False when it is not a merged-away memory."""
    row = await (await conn.execute(
        "SELECT merged_into FROM memory_items WHERE project_id = %s AND id = %s AND status = 'retired' "
        "AND retired_reason = 'merged' FOR UPDATE", (project_id, memory_id))).fetchone()
    if not row:
        return False
    kept = row["merged_into"]
    await conn.execute("UPDATE memory_items SET status = 'active', retired_reason = NULL, merged_into = NULL "
                       "WHERE project_id = %s AND id = %s", (project_id, memory_id))
    if kept:
        merge = await (await conn.execute(
            "SELECT detail FROM memory_events WHERE project_id = %s AND memory_id = %s AND kind = 'merged' "
            "AND detail->>'from' = %s ORDER BY at DESC, id DESC LIMIT 1", (project_id, kept, str(memory_id)))).fetchone()
        for episode_id, digest in (merge["detail"].get("evidence") or []) if merge else []:
            await conn.execute("DELETE FROM evidence WHERE memory_id = %s AND episode_id = %s AND md5(quote) = %s",
                               (kept, episode_id, digest))
    await events.record_batch(conn, project_id, [
        {"kind": "unmerged", "memory_id": memory_id, "detail": {"from": kept} if kept else {}},
        *([{"kind": "unmerged", "memory_id": kept, "detail": {"related": memory_id}}] if kept else []),
    ], actor=actor)
    return True


async def clear_flag(conn, project_id: UUID, memory_id: UUID, *, actor: str, reason: str) -> bool:
    """A person dismissed the flag (reason 'dismissed') or kept the memory ('kept')."""
    row = await (await conn.execute(
        "UPDATE memory_items m SET flag = NULL, flag_reason = NULL, flag_related = NULL "
        "FROM (SELECT id, flag FROM memory_items WHERE project_id = %s AND id = %s FOR UPDATE) old "
        "WHERE m.id = old.id AND old.flag IS NOT NULL RETURNING old.flag", (project_id, memory_id))).fetchone()
    if not row:
        return False
    await events.record(conn, project_id, "unflagged", memory_id=memory_id, actor=actor,
                        detail={"flag": row["flag"], "reason": reason})
    return True
