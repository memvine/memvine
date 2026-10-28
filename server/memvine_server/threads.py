"""Threads: one living page per piece of work, edited in place.

A thread is a release, task, PR, ticket or topic. Its identity comes first from what the text
names, deterministically and before any model call: the git branch in a session label, PR
numbers, ticket ids, release versions. Those keys load the existing pages, and the model returns
each changed page whole. Every item on a page carries evidence: a quote that is verified against
the episode (or kept unchanged from the old page), the same rule knowledge items follow.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from uuid import UUID

from .embeddings import Embedder
from .extraction.extract import normalize, parse_json, quote_in_source
from .extraction.llm import LLM
from .extraction.prompts import THREADS_SYSTEM, threads_user

SECTION_LABELS = ["Goal", "State", "Contains", "Done", "Decisions", "Left out", "Deferred", "Open questions",
                  "Next steps", "Outcome"]
KINDS = {"release", "task", "pr", "ticket", "topic"}
STATUSES = {"open", "blocked", "done", "abandoned"}
PREFIX_KIND = {"branch": "task", "pr": "pr", "ticket": "ticket", "release": "release", "topic": "topic"}
MAX_ITEMS = 8
# State is where things stand now, not a log: a real page grew eight State lines of SHAs and test counts.
MAX_STATE = 3
# Done is outcomes a teammate cares about, not a log: a real page grew eight Done lines of test counts.
SECTION_CAPS = {"State": MAX_STATE, "Done": 4}
# Progress expires: State and Next steps say where the work stands now. An item there whose newest
# source is more than this many days older than the episode being written is dropped at that
# rewrite, whatever the model returned; a newer source citing it keeps it.
PROGRESS_SECTIONS = {"State", "Next steps"}
PROGRESS_TTL = timedelta(days=30)
MAX_KEYS = 12
MAX_LOADED = 5
MAX_ALIASES = 20

TRIVIAL_BRANCHES = {"main", "master", "head", "develop"}
PR = re.compile(r"(?:\bPR\s*#?\s*|\bpull request\s*#?\s*|/pull/|(?<![\w&#])#)(\d{1,6})\b", re.IGNORECASE)
TICKET = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
# Look like ticket ids but are standards and algorithms.
NOT_TICKETS = {"UTF", "SHA", "ISO", "RFC", "HTTP", "TLS", "SSL", "CVE", "GPT", "MD", "AES", "RSA", "IPV"}
RELEASE = re.compile(r"(?<![\w.])(v?)(\d+\.\d+\.\d+)(?![\w]|\.\d)", re.IGNORECASE)
# A bare x.y.z in prose is as often a dependency's version as a release; it counts only right
# after one of these words ("release 4.2.0", "hotfix-4.2.1"), written v4.2.0, or in a branch name.
RELEASE_CUE = re.compile(r"\b(release|version|hotfix|tag)[\s:-]*$", re.IGNORECASE)
# A release branch names its version with dots or dashes, maybe after a product word:
# task/release-4-2-0, release-billing-v4.2.0, release/4.2.0.
RELEASE_BRANCH = re.compile(r"release[-_/]?(?:[a-z][a-z0-9]*[-_/])?v?(\d+)[.-](\d+)[.-](\d+)(?!\d)", re.IGNORECASE)

THREAD_COLUMNS = "id, kind, key, title, status, summary, sections, aliases, first_seen_at, last_activity_at"


# ------------------------------------------------------------------ identity


def branch_from_label(label: str | None) -> str | None:
    """The git branch of a session label, `Claude Code · <folder> · <branch>` (maybe followed by
    "(imported)"). None for other labels and for trunk branches, which name no piece of work."""
    if not label:
        return None
    text = re.sub(r"\s*\(imported\)\s*$", "", label.strip(), flags=re.IGNORECASE)
    parts = [p.strip() for p in text.split("·")]
    if len(parts) < 3:
        return None
    branch = parts[-1]
    if not branch or " " in branch or branch.lower() in TRIVIAL_BRANCHES:
        return None
    return branch


def _keys_in(text: str, *, branch: bool = False) -> list[str]:
    """pr:/ticket:/release: keys in order of first appearance. In a branch name every x.y.z is a release."""
    found: list[tuple[int, str]] = []
    for m in PR.finditer(text):
        found.append((m.start(), f"pr:{int(m.group(1))}"))
    for m in TICKET.finditer(text):
        if m.group(1).split("-")[0] not in NOT_TICKETS:
            found.append((m.start(), f"ticket:{m.group(1)}"))
    for m in RELEASE.finditer(text):
        if branch or m.group(1) or RELEASE_CUE.search(text[max(0, m.start() - 20):m.start()]):
            found.append((m.start(), f"release:{m.group(2)}"))
    return [key for _, key in sorted(found)]


def release_of_branch(branch: str | None) -> str | None:
    """The release version a branch name carries ("task/release-4-2-0" -> "4.2.0"), else None."""
    m = RELEASE_BRANCH.search(branch or "")
    return ".".join(str(int(g)) for g in m.groups()) if m else None


def derive_keys(label: str | None, content: str) -> list[str]:
    """Candidate thread keys for an episode, before any model call: the release its branch is for,
    its branch, then keys inside the branch name, then keys the text names."""
    keys: list[str] = []
    branch = branch_from_label(label)
    if branch:
        if version := release_of_branch(branch):
            keys.append(f"release:{version}")
        keys.append(f"branch:{branch}")
        keys += _keys_in(branch, branch=True)
    keys += _keys_in(content or "")
    out: list[str] = []
    for key in keys:
        if key not in out:
            out.append(key)
    return out[:MAX_KEYS]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def normalize_key(key) -> str | None:
    """A key in canonical form (`pr:440`, `ticket:INV-255`, `release:4.2.0`), or a topic slug."""
    key = str(key or "").strip()
    if not key:
        return None
    prefix, sep, rest = key.partition(":")
    prefix, rest = prefix.strip().lower(), rest.strip()
    if not sep or not rest or prefix not in PREFIX_KIND:
        slug = _slug(key)
        return f"topic:{slug}" if slug else None
    if prefix == "pr":
        digits = re.sub(r"\D", "", rest)
        return f"pr:{int(digits)}" if digits else None
    if prefix == "ticket":
        return f"ticket:{rest.upper()}"
    if prefix == "release":
        return f"release:{rest[1:] if rest[:1] in 'vV' and rest[1:2].isdigit() else rest}"
    if prefix == "topic":
        slug = _slug(rest)
        return f"topic:{slug}" if slug else None
    return f"branch:{rest}"


# ------------------------------------------------------------------ pages


def _as_list(value) -> list:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, list) else []


def _date(value) -> str | None:
    try:
        return date.fromisoformat(str(value)[:10]).isoformat() if value else None
    except ValueError:
        return None


def _label(value) -> str | None:
    wanted = str(value or "").strip().lower()
    return next((label for label in SECTION_LABELS if label.lower() == wanted), None)


def _evidence_set(sections: list) -> set[tuple[str, str]]:
    return {(str(ev.get("episode_id")), normalize(str(ev.get("quote", ""))))
            for sec in sections for item in sec.get("items") or [] for ev in item.get("evidence") or []}


def clean_sections(raw_sections, source: str, episode_id: str, old_sections: list) -> tuple[list[dict], int]:
    """Sections in vocabulary order with verified evidence only. A quote counts when it is in this
    episode's text, or when it is kept exactly as the old page had it. Returns (sections, items
    dropped for lack of evidence)."""
    kept_before = _evidence_set(old_sections)
    by_label: dict[str, list[dict]] = {}
    dropped = 0
    for sec in raw_sections if isinstance(raw_sections, list) else []:
        label = _label(sec.get("label")) if isinstance(sec, dict) else None
        if not label:
            continue
        for item in sec.get("items") or []:
            if not isinstance(item, dict) or not str(item.get("text", "")).strip():
                continue
            evidence, seen = [], set()
            for ev in item.get("evidence") or []:
                if not isinstance(ev, dict):
                    continue
                quote = str(ev.get("quote", "")).strip()
                old_id = str(ev.get("episode_id") or "")
                if old_id and old_id != episode_id and (old_id, normalize(quote)) in kept_before:
                    found = (old_id, quote)
                elif quote_in_source(quote, source):
                    found = (episode_id, quote[:300])
                else:
                    continue
                if (found[0], normalize(found[1])) not in seen:
                    seen.add((found[0], normalize(found[1])))
                    evidence.append({"episode_id": found[0], "quote": found[1]})
            if not evidence:
                dropped += 1
                continue
            text = str(item["text"]).strip()
            if label in LOG_PRONE:
                text = strip_log_noise(text)
            by_label.setdefault(label, []).append({"text": text[:500], "date": _date(item.get("date")), "evidence": evidence[:3]})
    return [{"label": label, "items": tidy(label, by_label[label])} for label in SECTION_LABELS if by_label.get(label)], dropped


def _newest(items: list[dict], cap: int = MAX_ITEMS) -> list[dict]:
    """At most `cap`, keeping the most recent when there are more, in their given order."""
    if len(items) <= cap:
        return items
    ranked = sorted(range(len(items)), key=lambda i: (items[i]["date"] or "", i), reverse=True)[:cap]
    return [items[i] for i in sorted(ranked)]


def tidy(label: str, items: list[dict]) -> list[dict]:
    """A section as stored: near-duplicates merged, then capped. State keeps its three newest
    lines, newest first; Done its four newest and other sections their eight newest, in page order."""
    items = dedupe(items)
    cap = SECTION_CAPS.get(label, MAX_ITEMS)
    if label != "State":
        return _newest(items, cap)
    order = sorted(range(len(items)), key=lambda i: (items[i].get("date") or "", i), reverse=True)
    return [items[i] for i in order[:cap]]


def expire_progress(sections: list[dict], at: datetime, source_dates: dict[str, datetime]) -> tuple[list[dict], int]:
    """`sections` without State and Next steps items whose newest source is more than 30 days
    before `at`. An item's age is that of its newest evidence episode (`source_dates`); the
    model-written `date` counts only when none of its evidence episodes is known. An item with
    neither is kept. Returns (sections, items dropped)."""
    cutoff = (at - PROGRESS_TTL).date()
    kept, dropped = [], 0
    for sec in sections:
        if sec["label"] not in PROGRESS_SECTIONS:
            kept.append(sec)
            continue
        items = []
        for item in sec["items"]:
            known = [source_dates[ev["episode_id"]].date() for ev in item.get("evidence") or []
                     if ev.get("episode_id") in source_dates]
            newest = max(known) if known else (date.fromisoformat(item["date"]) if item.get("date") else None)
            if newest is not None and newest < cutoff:
                dropped += 1
            else:
                items.append(item)
        if items:
            kept.append({**sec, "items": items})
    return kept, dropped


async def _source_dates(conn, project_id: UUID, sections: list[dict]) -> dict[str, datetime]:
    ids = sorted({str(ev["episode_id"]) for sec in sections if sec["label"] in PROGRESS_SECTIONS
                  for item in sec["items"] for ev in item.get("evidence") or [] if ev.get("episode_id")})
    if not ids:
        return {}
    rows = await (await conn.execute("SELECT id, occurred_at FROM episodes WHERE project_id = %s AND id = ANY(%s)",
                                     (project_id, ids))).fetchall()
    return {r["id"]: r["occurred_at"] for r in rows}


# ------------------------------------------------------------------ log noise
# Done, Contains and State items arrive carrying commit SHAs and test counts ("Release PR #454
# opened to master (non-draft, head release-billing-v4.2.0 at 5b7c3e9f2, no new commits)"). The
# prompt asks for outcomes; this conservative cleanup takes off what still slips through, and only
# where it is clearly an aside: inside parentheses, or right after "as"/"at".

LOG_PRONE = {"Done", "Contains", "State"}
_HASH = r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b"
_COUNT = (r"\b\d+\s*/\s*\d+\s+(?:components?|tests?|checks?|specs?|suites?)\b"
          r"|\b\d+(?:\.\d+)?%\s+coverage(?:\s+on\s+[a-z][\w -]*?(?=\s*(?:[,;)]|$)))?")
_FRAGMENT = rf"(?:{_HASH}|{_COUNT})"
_FRAGMENTS = rf"{_FRAGMENT}(?:\s*(?:,|and)\s*{_FRAGMENT})*"
_AFTER_AS_AT = re.compile(rf"\s+(?:as|at)\s+{_FRAGMENTS}", re.IGNORECASE)
_IN_PART = re.compile(rf"(?:^|\s+)(?:(?:as|at)\s+)?{_FRAGMENTS}", re.IGNORECASE)
_PARENS = re.compile(r"\s*\(([^()]*)\)")
MIN_CLEAN_WORDS = 4


def _clean_parens(m: re.Match) -> str:
    parts = [_IN_PART.sub("", part).strip() for part in re.split(r"[,;]", m.group(1))]
    kept = [p for p in parts if p]
    if kept == [p.strip() for p in re.split(r"[,;]", m.group(1))]:
        return m.group(0)  # nothing to take out: leave it exactly as written
    return f" ({', '.join(kept)})" if kept else ""


def strip_log_noise(text: str) -> str:
    """`text` without commit hashes and test/component/coverage counts that sit in parentheses or
    after "as"/"at". Kept as written when the cleanup would leave fewer than four words."""
    cleaned = _AFTER_AS_AT.sub("", _PARENS.sub(_clean_parens, text))
    cleaned = re.sub(r"\s+([,.;:])", r"\1", re.sub(r"\s{2,}", " ", cleaned)).strip()
    return cleaned if len(cleaned.split()) >= MIN_CLEAN_WORDS else text


def merge_sections(old: list, new: list) -> list[dict]:
    """For a page the model did not see: old items, then the new ones, per label."""
    by_label: dict[str, list[dict]] = {}
    for sec in [*old, *new]:
        by_label.setdefault(sec["label"], []).extend(sec["items"])
    return [{"label": label, "items": tidy(label, by_label[label])} for label in SECTION_LABELS if by_label.get(label)]


# ------------------------------------------------------------------ near-duplicates
# The model restates an item in new words instead of editing the old one ("The user merges #454
# into master." beside "The user merges release PR #454 into master, which releases 4.2.0."), so
# pages collect pairs. Word-set overlap catches them without another model call.

DUPLICATE_JACCARD = 0.55
STOPWORDS = frozenset("""
a an the of to in on at for by with from into onto and or but if then than that this these those is are was were be been
being it its as so do does did done whether which who whom what when where why how will would should shall can could may
might must has have had having there their they them we us our you your i he she his her about after before over under up
out also just only still yet once both each any all some such very via per pr pull request
""".split())
# PR numbers (after "PR 454" / "pull request #454" become "#454"), ticket ids and versions are
# kept whole; an item about #445 is never the same item as one about #446.
_PR_REF = re.compile(r"\b(?:pr|pull request)\s*#?\s*(\d+)\b")
_TOKEN = re.compile(r"#\d+|[a-z][a-z0-9]*-\d+\b|\d+(?:\.\d+)+|[a-z0-9]+")
_IDENTIFIER = re.compile(r"#\d+|[a-z][a-z0-9]*-\d+|\d+(?:\.\d+)+")


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def item_words(text: str) -> frozenset[str]:
    """An item's comparable words: lowercase, plurals folded, stopwords, single letters and bare
    numbers (counts, dates) dropped; PR numbers, tickets and versions kept as tokens."""
    text = _PR_REF.sub(r"#\1", normalize(text))
    words = set()
    for tok in _TOKEN.findall(text):
        if _IDENTIFIER.fullmatch(tok):
            words.add(tok)
        elif not tok.isdigit() and len(tok) > 1 and tok not in STOPWORDS:
            words.add(_stem(tok))
    return frozenset(words)


def same_item(a: frozenset[str], b: frozenset[str]) -> bool:
    """Jaccard at or over the threshold, unless both name identifiers and share none."""
    if not a or not b:
        return a == b
    ids_a, ids_b = {w for w in a if _IDENTIFIER.fullmatch(w)}, {w for w in b if _IDENTIFIER.fullmatch(w)}
    if ids_a and ids_b and not ids_a & ids_b:
        return False
    return len(a & b) / len(a | b) >= DUPLICATE_JACCARD


def dedupe(items: list[dict]) -> list[dict]:
    """Items of one section with near-duplicates merged: of two, the newer (by date, then position)
    keeps its place in the list of the first, and their evidence is pooled."""
    kept: list[tuple[frozenset[str], int, dict]] = []
    for pos, item in enumerate(items):
        words = item_words(item.get("text", ""))
        match = next((k for k, (w, _, _) in enumerate(kept) if same_item(w, words)), None)
        if match is None:
            kept.append((words, pos, item))
            continue
        old_words, old_pos, old = kept[match]
        newer, older = (item, old) if (item.get("date") or "", pos) >= (old.get("date") or "", old_pos) else (old, item)
        evidence, seen = [], set()
        for ev in [*(newer.get("evidence") or []), *(older.get("evidence") or [])]:
            mark = (str(ev.get("episode_id")), normalize(str(ev.get("quote", ""))))
            if mark not in seen:
                seen.add(mark)
                evidence.append(ev)
        merged = {**newer, "evidence": evidence[:3]}
        kept[match] = (item_words(merged.get("text", "")), max(pos, old_pos), merged)
    return [item for *_, item in kept]


def page_for_model(row) -> dict:
    return {"key": row["key"], "kind": row["kind"], "title": row["title"], "status": row["status"],
            "summary": row["summary"], "sections": _as_list(row["sections"]), "aliases": list(row["aliases"] or [])}


# When a whole page does not fit a brief, these sections say what an agent needs to act, in order.
BRIEF_SECTIONS = ("State", "Open questions", "Next steps", "Decisions", "Deferred", "Left out")
BRIEF_ITEMS = 4


def render_page_brief(row) -> str:
    """A trimmed page for a tight brief: title, status, summary and the sections that steer the
    next action (newest items first, a few each); Goal, Contains and Done are left to the full page."""
    by_label = {sec["label"]: sec["items"] for sec in _as_list(row["sections"])}
    sections = [{"label": label, "items": sorted(by_label[label], key=lambda i: i.get("date") or "", reverse=True)[:BRIEF_ITEMS]}
                for label in BRIEF_SECTIONS if by_label.get(label)]
    return render_page({**row, "sections": sections})


def render_page(row) -> str:
    """A page as recall shows it: title, status, summary, then the sections."""
    lines = [f"### {row['title']} [{row['status']}] ({row['key']})"]
    if row["summary"]:
        lines.append(row["summary"])
    for sec in _as_list(row["sections"]):
        lines.append(f"{sec['label']}:")
        for item in sec["items"]:
            when = f" ({datetime.fromisoformat(item['date']).strftime('%-d %b')})" if item.get("date") else ""
            lines.append(f"- {item['text']}{when}")
    return "\n".join(lines)


# ------------------------------------------------------------------ the update step


async def load_threads(conn, project_id: UUID, keys: list[str], session_id: str | None) -> list[dict]:
    """Existing threads named by the candidate keys (as key or alias), or already written from
    another part of the same session."""
    return await (await conn.execute(
        f"""
        SELECT {THREAD_COLUMNS} FROM threads t
        WHERE t.project_id = %s AND (t.key = ANY(%s) OR t.aliases && %s OR t.id IN (
            SELECT s.thread_id FROM thread_sources s JOIN episodes e ON e.project_id = s.project_id AND e.id = s.episode_id
            WHERE s.project_id = %s AND e.metadata->>'session_id' = %s))
        ORDER BY t.last_activity_at DESC NULLS LAST LIMIT %s
        """, (project_id, keys, keys, project_id, session_id, MAX_LOADED))).fetchall()


async def _find(conn, project_id: UUID, key: str):
    return await (await conn.execute(
        f"SELECT {THREAD_COLUMNS} FROM threads WHERE project_id = %s AND (key = %s OR %s = ANY(aliases)) "
        "ORDER BY (key = %s) DESC LIMIT 1", (project_id, key, key, key))).fetchone()


async def update_threads(conn, llm: LLM, embedder: Embedder, project_id: UUID, ep, project: str) -> tuple[list[dict], dict]:
    """Ask the model for the changed pages of this episode's threads and store them. Returns the
    threads written ({id, key, title, aliases}) and counts for the job result."""
    metadata = ep["metadata"] or {}
    keys = derive_keys(ep["label"], ep["content"])
    loaded = await load_threads(conn, project_id, keys, metadata.get("session_id"))
    raw = await llm.complete(THREADS_SYSTEM, threads_user(ep["label"] or ep["id"], ep["occurred_at"].isoformat(), ep["content"],
                                                          project, keys, [page_for_model(r) for r in loaded]), max_tokens=8192)
    data = parse_json(raw)
    pages = data.get("threads") if isinstance(data, dict) else data
    if not isinstance(pages, list):
        raise ValueError("thread update output has no threads list")

    by_key: dict[str, dict] = {}
    for r in loaded:
        for alias in r["aliases"] or []:
            by_key.setdefault(alias, r)
    by_key |= {r["key"]: r for r in loaded}  # a thread's own key beats another's alias
    loaded_ids = {r["id"] for r in loaded}
    stats = {"pages": 0, "created": 0, "dropped_no_quote": 0, "ignored": 0, "merged": 0, "expired": 0}
    groups = await _group_pages(conn, project_id, pages, by_key, stats)

    written: list[dict] = []
    related: list[tuple[UUID, list[str]]] = []
    for key, (old, entry, whole) in groups.items():
        # The model saw this page and returned it whole: its version replaces the stored one.
        seen = old is not None and old["id"] in loaded_ids and whole
        old_sections = _as_list(old["sections"]) if old else []
        sections, dropped = clean_sections(entry.get("sections"), ep["content"], ep["id"], old_sections if seen else [])
        stats["dropped_no_quote"] += dropped
        if not sections:  # nothing evidenced: never wipe a page or start an empty one
            stats["ignored"] += 1
            continue
        if old and not seen:
            sections = merge_sections(old_sections, sections)
        sections, expired = expire_progress(sections, ep["occurred_at"], await _source_dates(conn, project_id, sections))
        stats["expired"] += expired
        if not sections:
            stats["ignored"] += 1
            continue
        thread_id = await _save(conn, embedder, project_id, ep, entry, key, sections, old)
        await _release_identity(conn, project_id, thread_id)
        stats["pages"] += 1
        stats["created"] += old is None
        row = await (await conn.execute("SELECT id, key, title, aliases FROM threads WHERE id = %s", (thread_id,))).fetchone()
        written.append(dict(row))
        related.append((thread_id, [k for k in (normalize_key(r) for r in entry.get("related") or []) if k]))

    # One thread written from a session on a branch: the branch names it from now on.
    branch = branch_from_label(ep["label"])
    if branch and len(written) == 1 and f"branch:{branch}" not in [written[0]["key"], *written[0]["aliases"]]:
        if not await _find(conn, project_id, f"branch:{branch}"):
            await conn.execute("UPDATE threads SET aliases = array_append(aliases, %s) WHERE id = %s",
                               (f"branch:{branch}", written[0]["id"]))
    for thread_id, keys_ in related:
        await _link(conn, project_id, thread_id, keys_)
    return written, stats


async def _group_pages(conn, project_id: UUID, pages: list, by_key: dict, stats: dict) -> dict[str, tuple]:
    """The model's pages by the thread they belong to: {key: (stored row or None, entry, whole)}.

    One piece of work gets one page. A page returned under a key that names an existing thread
    (as its key or an alias) belongs to that thread. A new page whose key another page lists in
    its aliases (in this response, so after this episode) is folded into that page: its sections
    join the holder's (dedupe and caps tidy them), its key becomes an alias, and the holder keeps
    its key and title. `whole` is true when the model returned the holder's page under its own
    key; pages only folded into a stored holder add to it rather than replace it."""
    entries: list[tuple[str, dict]] = []
    for entry in pages:
        key = normalize_key(entry.get("key")) if isinstance(entry, dict) else None
        if key:
            entries.append((key, entry))
        else:
            stats["ignored"] += 1
    listed: dict[str, str] = {}  # alias -> key of the page in this response that lists it
    for key, entry in entries:
        for alias in (normalize_key(a) for a in entry.get("aliases") or []):
            if alias and alias != key:
                listed.setdefault(alias, key)

    async def stored(key: str):
        return by_key.get(key) or await _find(conn, project_id, key)

    targets: list[tuple[str, object, str, dict]] = []  # (holder key, stored row, own key, entry)
    for key, entry in entries:
        holder, old, visited = key, await stored(key), {key}
        while old is None and holder in listed and listed[holder] not in visited:  # a new page another page claims
            holder = listed[holder]
            visited.add(holder)
            old = await stored(holder)
        targets.append((old["key"] if old else holder, old, key, entry))

    out: dict[str, tuple] = {}
    for holder in dict.fromkeys(t[0] for t in targets):
        mine = [t for t in targets if t[0] == holder]
        base = next((t for t in mine if t[2] == holder), mine[0])
        entry = {**base[3], "sections": list(base[3].get("sections") or []), "aliases": list(base[3].get("aliases") or []),
                 "related": list(base[3].get("related") or [])}
        if base[2] != holder:  # the page came under an alias: the holder keeps its title
            entry["retitle"] = False
            entry["aliases"].append(base[2])
        for _, _, own, other in (t for t in mine if t is not base):
            stats["merged"] += 1
            entry["sections"] += [sec for sec in other.get("sections") or [] if isinstance(sec, dict)]
            entry["aliases"] += [own, *(other.get("aliases") or [])]
            entry["related"] += list(other.get("related") or [])
        entry["aliases"] = [a for a in dict.fromkeys(normalize_key(a) for a in entry["aliases"]) if a and a != holder]
        entry["related"] = [r for r in dict.fromkeys(normalize_key(r) for r in entry["related"])
                            if r and r != holder and r not in entry["aliases"]]
        out[holder] = (mine[0][1], entry, base[2] == holder)
    return out


async def _release_identity(conn, project_id: UUID, thread_id: UUID) -> None:
    """A branch-keyed page whose branch names a release version is that release's page: kind
    release, title "Release <v>", re-keyed to `release:<v>` with the branch as an alias, unless
    another thread holds that key, in which case only kind and title change."""
    row = await (await conn.execute("SELECT key, title, aliases FROM threads WHERE id = %s", (thread_id,))).fetchone()
    prefix, _, branch = row["key"].partition(":")
    version = release_of_branch(branch) if prefix == "branch" else None
    if not version:
        return
    release = f"release:{version}"
    title = stable_title(release, None, row["title"], False)
    taken = await (await conn.execute(
        "SELECT 1 FROM threads WHERE project_id = %s AND id <> %s AND (key = %s OR %s = ANY(aliases))",
        (project_id, thread_id, release, release))).fetchone()
    if taken:
        await conn.execute("UPDATE threads SET kind = 'release', title = %s WHERE id = %s", (title, thread_id))
        return
    aliases = [a for a in row["aliases"] or [] if a != release] + [row["key"]]
    await conn.execute("UPDATE threads SET kind = 'release', key = %s, title = %s, aliases = %s WHERE id = %s",
                       (release, title, aliases[:MAX_ALIASES], thread_id))


def stable_kind(key: str, proposed, old_kind: str | None) -> str:
    """The key decides the kind of a release, PR, ticket or branch; the model chooses only for a
    topic. A release page once turned into a PR page because its last episode was about the PR."""
    prefix = key.split(":", 1)[0]
    if prefix != "topic":
        return PREFIX_KIND[prefix]
    proposed = str(proposed or "").strip().lower()
    return proposed if proposed in KINDS else (old_kind or "topic")


def stable_title(key: str, proposed, old_title: str | None, retitle: bool) -> str:
    """An existing page keeps its title unless the model says the work itself changed; a new one
    takes the model's. A release is always "Release <v>", or "Release <v> (<product>)"."""
    title = str((old_title if old_title and not retitle else proposed) or old_title or key).strip()[:160]
    if key.startswith("release:"):
        version = key.split(":", 1)[1]
        product = re.fullmatch(rf"release {re.escape(version)} (\([^()]+\))", title, flags=re.IGNORECASE)
        # A product name, not a branch or path the model copied in ("(task/release-4-2-0)").
        if product and not re.search(r"[/_]|\d", product.group(1)):
            return f"Release {version} {product.group(1)}"
        return f"Release {version}"
    return title


async def _save(conn, embedder: Embedder, project_id: UUID, ep, entry: dict, key: str, sections: list, old) -> UUID:
    kind = stable_kind(key, entry.get("kind"), old["kind"] if old else None)
    status = str(entry.get("status", "")).strip().lower()
    status = status if status in STATUSES else (old["status"] if old else "open")
    title = stable_title(key, entry.get("title"), old["title"] if old else None, entry.get("retitle") is True)
    summary = str(entry.get("summary") or (old["summary"] if old else "")).strip()[:800]
    aliases = [*(old["aliases"] if old else [])]
    for alias in (normalize_key(a) for a in entry.get("aliases") or []):
        if alias and alias != (old["key"] if old else key) and alias not in aliases:
            aliases.append(alias)
    embedding = (await embedder.embed([f"{title}. {summary}"]))[0]
    at = ep["occurred_at"]
    if old:
        await conn.execute(
            """
            UPDATE threads SET kind = %s, title = %s, status = %s, summary = %s, sections = %s, aliases = %s, embedding = %s,
                first_seen_at = LEAST(COALESCE(first_seen_at, %s), %s), last_activity_at = GREATEST(COALESCE(last_activity_at, %s), %s),
                updated_at = now()
            WHERE id = %s
            """, (kind, title, status, summary, json.dumps(sections), aliases[:MAX_ALIASES], embedding, at, at, at, at, old["id"]))
        thread_id = old["id"]
    else:
        thread_id = (await (await conn.execute(
            """
            INSERT INTO threads (project_id, kind, key, title, status, summary, sections, aliases, embedding, first_seen_at, last_activity_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (project_id, kind, key, title, status, summary, json.dumps(sections), aliases[:MAX_ALIASES], embedding, at, at),
        )).fetchone())["id"]
    await conn.execute("INSERT INTO thread_sources (thread_id, project_id, episode_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                       (thread_id, project_id, ep["id"]))
    return thread_id


async def _link(conn, project_id: UUID, thread_id: UUID, keys: list[str]) -> None:
    if not keys:
        return
    for row in await (await conn.execute(
            "SELECT id FROM threads WHERE project_id = %s AND id <> %s AND (key = ANY(%s) OR aliases && %s)",
            (project_id, thread_id, keys, keys))).fetchall():
        await conn.execute("INSERT INTO thread_links (project_id, thread_id, related_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                           (project_id, thread_id, row["id"]))


async def resolve_thread(conn, project_id: UUID, key: str | None, written: list[dict]) -> UUID | None:
    """The thread a knowledge item names: one written from this episode, else any by key or alias."""
    key = normalize_key(key) if key else None
    if not key:
        return None
    for t in written:
        if key == t["key"] or key in (t["aliases"] or []):
            return t["id"]
    row = await _find(conn, project_id, key)
    return row["id"] if row else None


async def forget_episode(conn, project_id: UUID, episode_id: str) -> int:
    """Take an episode's evidence off every page it fed (it is being re-sent, retried or deleted):
    items left without evidence go, and a thread left without sources is deleted. Returns how
    many threads were deleted."""
    rows = await (await conn.execute(
        "SELECT t.id, t.sections FROM threads t JOIN thread_sources s ON s.thread_id = t.id "
        "WHERE s.project_id = %s AND s.episode_id = %s", (project_id, episode_id))).fetchall()
    if not rows:
        return 0
    for row in rows:
        sections = []
        for sec in _as_list(row["sections"]):
            items = []
            for item in sec["items"]:
                evidence = [ev for ev in item.get("evidence") or [] if ev.get("episode_id") != episode_id]
                if evidence:
                    items.append({**item, "evidence": evidence})
            if items:
                sections.append({**sec, "items": items})
        await conn.execute("UPDATE threads SET sections = %s, updated_at = now() WHERE id = %s", (json.dumps(sections), row["id"]))
    await conn.execute("DELETE FROM thread_sources WHERE project_id = %s AND episode_id = %s", (project_id, episode_id))
    gone = await (await conn.execute(
        "DELETE FROM threads t WHERE t.project_id = %s AND t.id = ANY(%s) "
        "AND NOT EXISTS (SELECT 1 FROM thread_sources s WHERE s.thread_id = t.id) RETURNING id",
        (project_id, [r["id"] for r in rows]))).fetchall()
    return len(gone)


# ------------------------------------------------------------------ reading


SUMMARY_SELECT = """
    SELECT t.id, t.kind, t.key, t.title, t.status, t.summary, t.first_seen_at, t.last_activity_at,
           (SELECT count(DISTINCT coalesce(e.metadata->>'session_id', e.id)) FROM thread_sources s
              JOIN episodes e ON e.project_id = s.project_id AND e.id = s.episode_id WHERE s.thread_id = t.id) AS session_count,
           (SELECT count(*) FROM memory_items m WHERE m.thread_id = t.id AND m.status IN ('active', 'stale')) AS knowledge_count
    FROM threads t
"""


async def list_threads(conn, project_id: UUID, *, status: str | None = None, kind: str | None = None, q: str | None = None,
                       limit: int = 50, offset: int = 0) -> list[dict]:
    clauses, params = ["t.project_id = %s"], [project_id]
    if status:
        clauses.append("t.status = %s")
        params.append(status)
    if kind:
        clauses.append("t.kind = %s")
        params.append(kind)
    if q:
        clauses.append("(t.title ILIKE %s OR t.summary ILIKE %s OR t.key ILIKE %s OR array_to_string(t.aliases, ' ') ILIKE %s)")
        params += [f"%{q}%"] * 4
    return await (await conn.execute(
        SUMMARY_SELECT + f" WHERE {' AND '.join(clauses)} ORDER BY t.last_activity_at DESC NULLS LAST, t.id LIMIT %s OFFSET %s",
        [*params, limit, offset])).fetchall()


async def get_thread(conn, project_id: UUID, thread_id: UUID) -> dict | None:
    """One thread: its summary fields, page sections, related threads, knowledge and sources."""
    from .store import list_memories

    row = await (await conn.execute(SUMMARY_SELECT + " WHERE t.project_id = %s AND t.id = %s", (project_id, thread_id))).fetchone()
    if not row:
        return None
    page = await (await conn.execute("SELECT sections, aliases FROM threads WHERE id = %s", (thread_id,))).fetchone()
    related = await (await conn.execute(
        SUMMARY_SELECT + " WHERE t.project_id = %s AND t.id IN (SELECT related_id FROM thread_links WHERE thread_id = %s "
        "UNION SELECT thread_id FROM thread_links WHERE related_id = %s) ORDER BY t.last_activity_at DESC NULLS LAST",
        (project_id, thread_id, thread_id))).fetchall()
    knowledge = [m for m in await list_memories(conn, project_id, status=None, kind=None, q=None, limit=200, offset=0,
                                                thread_id=thread_id) if m.status in ("active", "stale")]
    sources = await (await conn.execute(
        "SELECT e.id AS episode_id, e.label, e.occurred_at FROM thread_sources s "
        "JOIN episodes e ON e.project_id = s.project_id AND e.id = s.episode_id WHERE s.thread_id = %s ORDER BY e.occurred_at",
        (thread_id,))).fetchall()
    return {**row, "aliases": list(page["aliases"] or []), "sections": _as_list(page["sections"]), "related": related,
            "knowledge": knowledge, "sources": sources}


THREAD_MIN_SIMILARITY = 0.45
MAX_RECALLED = 2


async def relevant_threads(conn, project_id: UUID, query: str, embedding: list[float], *, session_id: str | None = None,
                           as_of: datetime | None = None, branch: str | None = None) -> list[dict]:
    """Up to two pages the question is about: the page of the caller's git branch first, then one
    the question names (a key, alias, branch or version in the query), else close to it in
    embedding space, with a nudge toward the caller's own session's work. A page written after
    `as_of` knows later facts, so it is left out for such a question."""
    first = await branch_thread(conn, project_id, branch, as_of)
    if first:
        first["branch_match"] = True  # recall counts this page as a hit (recall.py)
    rest = await _relevant(conn, project_id, query, embedding, session_id=session_id, as_of=as_of)
    out: list[dict] = []
    for row in [first, *rest] if first else rest:
        # One piece of work is one page: never a page beside another that lists it as an alias.
        if not any(row["id"] == o["id"] or _same_work(row, o) for o in out):
            out.append(row)
    return out[:MAX_RECALLED]


def _same_work(a, b) -> bool:
    return a["key"] in (b["aliases"] or []) or b["key"] in (a["aliases"] or [])


async def branch_thread(conn, project_id: UUID, branch: str | None, as_of: datetime | None) -> dict | None:
    """The page of the branch the caller is on: keyed `branch:<name>` or carrying it as an alias.
    Trunk branches name no piece of work."""
    branch = (branch or "").strip()
    if not branch or branch.lower() in TRIVIAL_BRANCHES:
        return None
    key = f"branch:{branch}"
    row = await (await conn.execute(
        f"SELECT {THREAD_COLUMNS} FROM threads WHERE project_id = %s AND (key = %s OR %s = ANY(aliases)) "
        "AND (%s::timestamptz IS NULL OR last_activity_at <= %s) ORDER BY (key = %s) DESC, last_activity_at DESC NULLS LAST LIMIT 1",
        (project_id, key, key, as_of, as_of, key))).fetchone()
    return dict(row) if row else None


async def _relevant(conn, project_id: UUID, query: str, embedding: list[float], *, session_id: str | None,
                    as_of: datetime | None) -> list[dict]:
    keys = derive_keys(None, query)
    rows = await (await conn.execute(
        f"""
        SELECT {THREAD_COLUMNS}, 1 - (t.embedding <=> %s::vector) AS similarity,
               (t.key = ANY(%s) OR t.aliases && %s OR (length(split_part(t.key, ':', 2)) >= 5
                AND strpos(lower(%s), lower(split_part(t.key, ':', 2))) > 0)) AS named,
               EXISTS (SELECT 1 FROM thread_sources s JOIN episodes e ON e.project_id = s.project_id AND e.id = s.episode_id
                       WHERE s.thread_id = t.id AND e.metadata->>'session_id' = %s) AS mine
        FROM threads t WHERE t.project_id = %s AND t.embedding IS NOT NULL
          AND (%s::timestamptz IS NULL OR t.last_activity_at <= %s)
        ORDER BY named DESC, t.embedding <=> %s::vector LIMIT 8
        """,
        (embedding, keys, keys, query, session_id, project_id, as_of, as_of, embedding))).fetchall()
    scored = []
    for r in rows:
        score = (r["similarity"] or 0) + (0.15 if r["mine"] else 0)
        if r["named"] or score >= THREAD_MIN_SIMILARITY:
            scored.append((not r["named"], -score, r))
    return [dict(r) for *_, r in sorted(scored, key=lambda t: t[:2])]
