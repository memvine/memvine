"""Profiles: a short, always-current summary per person and per project, served at session start.

A profile is derived from memory, never a source of truth. The fast model restates a handful of
current memories in a few lines, each line citing the memories it came from; lines that cite
nothing or cite a memory it was not given are dropped (`validate`), and so is a line with a number
none of its memories has. Serving re-checks every citation (`current_lines`), so a forgotten,
superseded or expired memory leaves the brief at once, before the rebuild.

- person profile (subject: an identity): their preferences, the rules and decisions they stated or
  own, their role and ownership: memories drawn from their own sources (evidence episode identity)
  that are preferences, rules or decisions, and memories whose statement or subjects name them.
  At most PERSON_LINES lines and PERSON_TOKENS tokens.
- project profile (subject ''): the project description (settings, shown as is), then the standing
  rules and the most durable decisions. At most PROJECT_LINES lines and PROJECT_TOKENS tokens in all.

Only project-audience memory goes in (a profile is shown to anyone in the project), only current
items (active or stale, not expired; stale lines say "may be outdated"), never thread-scoped
items, events or plans: no progress or status.

Freshness (`sweep`, from the worker's idle loop, at most every SWEEP_SECONDS): a profile turns
dirty when a memory it cites is no longer current, or when memory_events after its
built_from_event_id touch a memory that is now one of its candidates; dirty profiles are queued
as 'profile' jobs at most every DEBOUNCE_MINUTES each. A profile is created (dirty) the first
time it is asked for, so people nobody asks about cost nothing.
"""
from __future__ import annotations

import json
import re
import time
from uuid import UUID

from . import events
from .extraction.extract import parse_json
from .extraction.llm import LLM, LLMError, fast
from .recall import NOT_EXPIRED, tokens_of

PERSON_LINES, PERSON_TOKENS = 6, 150
PROJECT_LINES, PROJECT_TOKENS = 8, 200
DESCRIPTION_CHARS = 300
LINE_CHARS = 240
CANDIDATES = 24
DEBOUNCE_MINUTES = 10
SWEEP_SECONDS = 60
OUTDATED = " (may be outdated)"
# Events that can change what a profile should say. 'expired' is never stored: a cited memory past
# its valid_to is caught by the citation check instead.
RELEVANT_KINDS = ["created", "superseded", "supersedes", "forgotten", "confirmed", "restored", "corrected",
                  "merged", "stale", "revalidated", "category_changed", "contradicted", "extended", "deleted"]
CURRENT = f"status IN ('active', 'stale') AND {NOT_EXPIRED}"
CURRENT_M = "m.status IN ('active', 'stale') AND (m.valid_to IS NULL OR m.valid_to > now())"

PROFILE_SYSTEM = """You write a short profile of one person or one project from a team's memory records. You get numbered MEMORIES ([m1], [m2], ...) and the subject. Restate what the memories say about the subject in a few short lines.

Return ONLY a JSON object: {"lines": [{"text": string, "cite": ["m1", ...]}]}

Rules:
- Only restate what the cited memories say. Never add a fact, name, number, reason or inference that is not in them, and never generalize beyond them.
- Every line cites every memory it draws on by its number. A line you cannot cite does not belong in the profile.
- A person's profile: their stated preferences, the rules and decisions they set or own, their role and what they own. Leave out memories that are not about this person or not set by them.
- A project's profile: its standing rules first, then its most durable decisions. The project description is context; do not restate it.
- Durable things only: no progress, status, work in flight, plans or dated events.
- One short sentence per line (under 25 words), no markdown, no ids in the text. Say each thing once: merge memories that say the same thing into one line citing all of them.
- Fewer lines beat padding. Return {"lines": []} when nothing qualifies.
- A memory marked (stale) may no longer be true; restate it only if it still matters."""


def job_key(subject_kind: str, subject: str) -> str:
    return f"{subject_kind}:{subject}"


def parse_key(key: str) -> tuple[str, str]:
    kind, _, subject = key.partition(":")
    return kind, subject


def name_words(identity: str) -> list[str]:
    """The words a statement would name a person by: "priya.sharma@northwind.dev" -> priya, sharma."""
    local = identity.split("@", 1)[0].lower()
    words = [w for w in re.split(r"[^a-z]+", local) if len(w) >= 3]
    return list(dict.fromkeys(words))[:3]


async def _custom_categories(conn, project_id: UUID) -> dict[str, str]:
    if not await (await conn.execute("SELECT to_regclass('public.project_categories') IS NOT NULL AS ok")).fetchone():
        return {}
    rows = await (await conn.execute(
        "SELECT slug, name FROM project_categories WHERE project_id = %s", (project_id,))).fetchall()
    return {r["slug"]: r["name"] for r in rows}


async def candidates(conn, project_id: UUID, subject_kind: str, subject: str) -> list[dict]:
    """The memories a profile may restate, most important first, at most CANDIDATES."""
    base = (f"FROM memory_items m WHERE m.project_id = %s AND m.audience = 'project' AND {CURRENT_M} "
            "AND m.scope IS DISTINCT FROM 'thread' AND m.kind NOT IN ('episodic', 'prospective')")
    columns = "m.id, m.statement, m.category, m.kind, m.status, m.standing, m.review, m.recall_count, m.recorded_at"
    if subject_kind == "project":
        custom = list(await _custom_categories(conn, project_id))
        rows = await (await conn.execute(
            f"SELECT {columns}, false AS by_subject {base} "
            "AND (m.standing OR m.category IN ('decision', 'rule') OR m.category = ANY(%s)) "
            "ORDER BY m.standing DESC, (m.review = 'kept') DESC, (m.category = 'rule') DESC, m.recall_count DESC, "
            "m.recorded_at DESC LIMIT %s", (project_id, custom, CANDIDATES))).fetchall()
        return list(rows)
    words = name_words(subject)
    pattern = r"\m(" + "|".join(words) + r")\M" if words else None
    rows = await (await conn.execute(
        f"""
        SELECT * FROM (
            SELECT {columns},
                   EXISTS (SELECT 1 FROM evidence v JOIN episodes e ON e.project_id = v.project_id AND e.id = v.episode_id
                           WHERE v.memory_id = m.id AND lower(e.identity) = lower(%s)) AS by_subject,
                   (%s::text IS NOT NULL AND (m.statement ~* %s OR EXISTS (
                        SELECT 1 FROM unnest(m.subjects) s WHERE lower(s) = ANY(%s::text[]) OR lower(s) = lower(%s)))) AS names_subject
            {base}
        ) c
        WHERE (by_subject AND (category IN ('preference', 'rule', 'decision') OR kind = 'preference')) OR names_subject
        ORDER BY (kind = 'preference' OR category = 'preference') DESC, names_subject DESC, (review = 'kept') DESC,
                 recorded_at DESC
        LIMIT %s
        """, (subject, pattern, pattern or "", words, subject, project_id, CANDIDATES))).fetchall()
    return list(rows)


def profile_user(subject_kind: str, subject: str, rows: list[dict], *, project_name: str = "",
                 description: str = "", custom: dict[str, str] | None = None) -> tuple[str, dict[str, UUID]]:
    """The prompt, and the map from each [m] number to its memory id."""
    custom = custom or {}
    ids: dict[str, UUID] = {}
    lines = []
    for n, r in enumerate(rows, 1):
        ids[f"m{n}"] = r["id"]
        what = custom.get(r["category"]) or r["category"] or ("preference" if r["kind"] == "preference" else "fact")
        tags = [what, *(["standing rule"] if r["standing"] else []), *(["stated by this person"] if r.get("by_subject") else []),
                *(["stale"] if r["status"] == "stale" else [])]
        lines.append(f"[m{n}] ({', '.join(tags)}) {r['statement']}")
    if subject_kind == "person":
        head = (f"PROFILE of person {subject}\nWrite at most {PERSON_LINES} lines, about {PERSON_TOKENS} tokens in all. "
                "\"The user\" in a memory stated by this person is this person.")
    else:
        head = (f"PROFILE of the project {project_name}\nWrite at most {PROJECT_LINES - 1} lines, about "
                f"{PROJECT_TOKENS - 40} tokens in all.\nProject description (context only): {description or '(none)'}")
    return f"{head}\n\nMEMORIES:\n" + "\n".join(lines), ids


def validate(raw: str, ids: dict[str, UUID], statements: dict[UUID, str], *, max_lines: int, max_tokens: int) -> list[dict]:
    """The model's lines, kept only when they cite memories they were given, carry no number none of
    those memories has, and fit the caps. Raises LLMError when the reply is not the JSON asked for."""
    try:
        data = parse_json(raw)
    except ValueError as exc:
        raise LLMError(f"profile was not valid JSON: {exc}") from exc
    items = data.get("lines") if isinstance(data, dict) else data if isinstance(data, list) else None
    if not isinstance(items, list):
        raise LLMError("profile JSON has no lines")
    out, spent, seen = [], 0, set()
    for item in items:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        cites = item.get("cite") or item.get("memory_ids") or []
        if isinstance(cites, str):
            cites = [cites]
        cited = list(dict.fromkeys(ids[c.strip("[] ")] for c in cites if isinstance(c, str) and c.strip("[] ") in ids))
        if not text or not cited or text.lower() in seen:
            continue
        if len(cited) < len([c for c in cites if isinstance(c, str)]):
            continue  # cites a memory it was not given: it may say something none of them says
        source = " ".join(statements[i] for i in cited)
        if any(num not in source for num in re.findall(r"\d+(?:[.,:]\d+)*", text)):
            continue  # a number no cited memory has
        if len(text) > LINE_CHARS:
            text = text[: LINE_CHARS - 1].rstrip() + "…"
        cost = tokens_of(text) + 2
        if len(out) >= max_lines or spent + cost > max_tokens:
            break
        seen.add(text.lower())
        spent += cost
        out.append({"text": text, "memory_ids": [str(i) for i in cited]})
    return out


async def _project(conn, project_id: UUID):
    return await (await conn.execute("SELECT name, description FROM projects WHERE id = %s", (project_id,))).fetchone()


def _description(project) -> str:
    text = " ".join((project["description"] or "").split()) if project else ""
    return text if len(text) <= DESCRIPTION_CHARS else text[: DESCRIPTION_CHARS - 1].rstrip() + "…"


async def build(conn, llm: LLM, project_id: UUID, subject_kind: str, subject: str) -> dict:
    """Rebuild one profile from current memory (the 'profile' job)."""
    through = (await (await conn.execute(
        "SELECT coalesce(max(id), 0) AS id FROM memory_events WHERE project_id = %s", (project_id,))).fetchone())["id"]
    rows = await candidates(conn, project_id, subject_kind, subject)
    project = await _project(conn, project_id)
    if subject_kind == "person":
        max_lines, max_tokens = PERSON_LINES, PERSON_TOKENS
    else:
        description = _description(project)
        max_lines = PROJECT_LINES - (1 if description else 0)
        max_tokens = PROJECT_TOKENS - (tokens_of(description) + 2 if description else 0)
    lines: list[dict] = []
    if rows:
        custom = await _custom_categories(conn, project_id) if subject_kind == "project" else {}
        user, ids = profile_user(subject_kind, subject, rows, project_name=project["name"] if project else "",
                                 description=_description(project), custom=custom)
        raw = await fast(llm).complete(PROFILE_SYSTEM, user, max_tokens=1000)
        lines = validate(raw, ids, {r["id"]: r["statement"] for r in rows}, max_lines=max_lines, max_tokens=max_tokens)
        stale = {str(r["id"]) for r in rows if r["status"] == "stale"}
        for line in lines:
            if stale.intersection(line["memory_ids"]):
                line["text"] += OUTDATED
    tokens = sum(tokens_of(line["text"]) + 2 for line in lines)
    await conn.execute(
        """
        INSERT INTO profiles (project_id, subject_kind, subject, lines, token_estimate, dirty, built_at, built_from_event_id)
        VALUES (%s, %s, %s, %s, %s, false, now(), %s)
        ON CONFLICT (project_id, subject_kind, subject) DO UPDATE SET lines = EXCLUDED.lines,
            token_estimate = EXCLUDED.token_estimate, dirty = false, built_at = now(),
            built_from_event_id = EXCLUDED.built_from_event_id
        """, (project_id, subject_kind, subject, json.dumps(lines), tokens, through))
    await events.record(conn, project_id, "profile_rebuilt", memory_id=None, actor=events.SYSTEM,
                        detail={"subject_kind": subject_kind, "subject": subject, "lines": len(lines), "from_memories": len(rows)})
    return {"subject_kind": subject_kind, "subject": subject, "lines": len(lines), "from_memories": len(rows), "tokens": tokens}


# ------------------------------------------------------------------ freshness

async def enqueue(conn, project_id: UUID, subject_kind: str, subject: str) -> bool:
    """Queue a rebuild unless one is already queued or running. True when queued."""
    row = await (await conn.execute(
        """
        INSERT INTO jobs (project_id, kind, episode_id, background)
        SELECT %s, 'profile', %s, true
        WHERE NOT EXISTS (SELECT 1 FROM jobs WHERE project_id = %s AND kind = 'profile' AND episode_id = %s
                          AND status IN ('queued', 'running'))
        RETURNING id
        """, (project_id, job_key(subject_kind, subject), project_id, job_key(subject_kind, subject)))).fetchone()
    return row is not None


async def ensure(conn, project_id: UUID, subject_kind: str, subject: str) -> None:
    """Create the profile (dirty, unbuilt) the first time it is asked for; the next sweep builds it."""
    await conn.execute(
        "INSERT INTO profiles (project_id, subject_kind, subject) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (project_id, subject_kind, subject))


_last_sweep = 0.0


async def sweep(conn, *, force: bool = False) -> dict:
    """Mark profiles dirty whose memory changed, and queue the dirty ones (debounced). Cheap SQL; runs
    from the worker's idle loop at most every SWEEP_SECONDS."""
    global _last_sweep
    if not force and time.monotonic() - _last_sweep < SWEEP_SECONDS:
        return {"skipped": True}
    _last_sweep = time.monotonic()
    # A cited memory is no longer current: forgotten, superseded, expired, deleted.
    cited = await (await conn.execute(
        f"""
        UPDATE profiles p SET dirty = true
        WHERE NOT p.dirty AND EXISTS (
            SELECT 1 FROM jsonb_array_elements(p.lines) l, jsonb_array_elements_text(l->'memory_ids') AS c(mid)
            LEFT JOIN memory_items m ON m.id = c.mid::uuid
            WHERE m.id IS NULL OR NOT ({CURRENT_M}))
        RETURNING 1
        """)).fetchall()
    # New events about memory that is now one of a profile's candidates (a new or replacing memory).
    marked = 0
    pending = await (await conn.execute(
        """
        SELECT p.project_id, p.subject_kind, p.subject, p.lines, p.built_from_event_id FROM profiles p
        WHERE NOT p.dirty AND EXISTS (SELECT 1 FROM memory_events e WHERE e.project_id = p.project_id
                                      AND e.id > p.built_from_event_id AND e.kind = ANY(%s))
        LIMIT 200
        """, (RELEVANT_KINDS,))).fetchall()
    for p in pending:
        changed = await (await conn.execute(
            "SELECT max(id) AS through, array_agg(DISTINCT memory_id) FILTER (WHERE memory_id IS NOT NULL) AS ids "
            "FROM memory_events WHERE project_id = %s AND id > %s AND kind = ANY(%s)",
            (p["project_id"], p["built_from_event_id"], RELEVANT_KINDS))).fetchone()
        touched = set(changed["ids"] or [])
        now_candidates = {r["id"] for r in await candidates(conn, p["project_id"], p["subject_kind"], p["subject"])}
        cited_ids = {UUID(i) for line in p["lines"] for i in line.get("memory_ids", [])}
        relevant = bool(touched & (now_candidates | cited_ids))
        await conn.execute(
            "UPDATE profiles SET dirty = dirty OR %s, built_from_event_id = CASE WHEN %s THEN built_from_event_id ELSE %s END "
            "WHERE project_id = %s AND subject_kind = %s AND subject = %s",
            (relevant, relevant, changed["through"], p["project_id"], p["subject_kind"], p["subject"]))
        marked += relevant
    # Queue dirty profiles, each at most every DEBOUNCE_MINUTES; one that keeps failing waits an hour.
    queued = await (await conn.execute(
        """
        INSERT INTO jobs (project_id, kind, episode_id, background)
        SELECT p.project_id, 'profile', p.subject_kind || ':' || p.subject, true FROM profiles p
        WHERE p.dirty AND (p.built_at IS NULL OR p.built_at < now() - make_interval(mins => %s))
          AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.project_id = p.project_id AND j.kind = 'profile'
                          AND j.episode_id = p.subject_kind || ':' || p.subject
                          AND (j.status IN ('queued', 'running') OR (j.status = 'failed' AND j.finished_at > now() - interval '1 hour')))
        RETURNING id
        """, (DEBOUNCE_MINUTES,))).fetchall()
    return {"dirty": len(cited) + marked, "queued": len(queued)}


# ------------------------------------------------------------------ serving

async def current_lines(conn, lines: list[dict]) -> list[dict]:
    """The lines whose every cited memory is still current (a forgotten memory leaves at once)."""
    ids = {i for line in lines for i in line.get("memory_ids", [])}
    if not ids:
        return []
    ok = {str(r["id"]) for r in await (await conn.execute(
        f"SELECT id FROM memory_items WHERE id = ANY(%s::uuid[]) AND {CURRENT}", (list(ids),))).fetchall()}
    return [line for line in lines if line.get("memory_ids") and set(line["memory_ids"]) <= ok]


async def get(conn, project_id: UUID, subject_kind: str, subject: str, *, create: bool = True) -> dict | None:
    """One profile as served: current lines, rendered text and its token count. Created (dirty,
    empty) when asked for the first time and `create`."""
    if create:
        await ensure(conn, project_id, subject_kind, subject)
    row = await (await conn.execute(
        "SELECT subject_kind, subject, lines, token_estimate, dirty, built_at, built_from_event_id FROM profiles "
        "WHERE project_id = %s AND subject_kind = %s AND subject = %s", (project_id, subject_kind, subject))).fetchone()
    if not row:
        return None
    return await _out(conn, project_id, row)


async def _out(conn, project_id: UUID, row) -> dict:
    lines = await current_lines(conn, row["lines"] or [])
    description = _description(await _project(conn, project_id)) if row["subject_kind"] == "project" else ""
    text = render(row["subject_kind"], row["subject"], lines, description)
    return {"subject_kind": row["subject_kind"], "subject": row["subject"], "lines": lines, "description": description or None,
            "text": text, "tokens": tokens_of(text) if text else 0, "dirty": row["dirty"], "built_at": row["built_at"]}


async def list_profiles(conn, project_id: UUID, subject_kind: str | None = None) -> list[dict]:
    rows = await (await conn.execute(
        "SELECT subject_kind, subject, lines, token_estimate, dirty, built_at, built_from_event_id FROM profiles "
        "WHERE project_id = %s AND (%s::text IS NULL OR subject_kind = %s) ORDER BY subject_kind DESC, subject LIMIT 500",
        (project_id, subject_kind, subject_kind))).fetchall()
    return [await _out(conn, project_id, r) for r in rows]


def render(subject_kind: str, subject: str, lines: list[dict], description: str = "") -> str:
    body = ([f"- {description}"] if description else []) + [f"- {line['text']}" for line in lines]
    if not body:
        return ""
    head = f"=== memvine · about {subject} ===" if subject_kind == "person" else "=== memvine · this project ==="
    return "\n".join([head, *body])


async def brief_sections(conn, project_id: UUID, identity: str | None) -> tuple[str, int]:
    """The caller's person profile, then the project profile: what a session-start brief opens with."""
    parts = []
    if identity:
        person = await get(conn, project_id, "person", identity)
        if person and person["text"]:
            parts.append(person["text"])
    project = await get(conn, project_id, "project", "")
    if project and project["text"]:
        parts.append(project["text"])
    text = "\n\n".join(parts)
    return text, tokens_of(text) if text else 0


async def rebuild(conn, project_id: UUID, subject_kind: str | None = None, subject: str | None = None) -> int:
    """Mark dirty and queue now, past the debounce: one profile, or every profile of the project
    (the project profile and each identity's are created when missing). Returns how many were queued."""
    if subject_kind:
        targets = [(subject_kind, subject or "")]
    else:
        await ensure(conn, project_id, "project", "")
        await conn.execute(
            "INSERT INTO profiles (project_id, subject_kind, subject) SELECT project_id, 'person', identity "
            "FROM identities WHERE project_id = %s ORDER BY last_seen_at DESC NULLS LAST LIMIT 200 ON CONFLICT DO NOTHING",
            (project_id,))
        targets = [(r["subject_kind"], r["subject"]) for r in await (await conn.execute(
            "SELECT subject_kind, subject FROM profiles WHERE project_id = %s", (project_id,))).fetchall()]
    queued = 0
    for kind, subj in targets:
        await ensure(conn, project_id, kind, subj)
        await conn.execute("UPDATE profiles SET dirty = true WHERE project_id = %s AND subject_kind = %s AND subject = %s",
                           (project_id, kind, subj))
        queued += await enqueue(conn, project_id, kind, subj)
    return queued
