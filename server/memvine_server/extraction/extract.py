"""Turn one episode into candidate memory items, keeping only those whose quote
really appears in the source text."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .llm import LLM
from .prompts import CONVERSATION_SYSTEM, KNOWLEDGE_SYSTEM, conversation_user, knowledge_user

KINDS = {"semantic", "episodic", "procedural", "prospective", "preference"}
ASSERTIONS = {"observed", "stated", "claimed", "inferred"}
CONFIDENCES = {"high", "medium", "low"}
SCOPES = {"project", "thread"}

# Agent self-reports that must never be stored as facts, whatever the model labelled them.
CLAIM_WORDS = re.compile(
    r"\b(implementation (is )?complete|all tests pass|tests? (are )?passing|successfully (implemented|fixed|deployed)|is now (done|working|fixed))\b",
    re.IGNORECASE,
)


@dataclass
class Candidate:
    statement: str
    kind: str
    subjects: list[str]
    assertion: str
    confidence: str
    valid_from: datetime | None
    quote: str
    standing: bool = False
    # Knowledge (KNOWLEDGE_SYSTEM): category, why it matters, the thread it belongs to.
    category: str | None = None
    why: str | None = None
    thread_key: str | None = None
    scope: str | None = None  # "project" (anyone in the project) or "thread" (only beside its thread's page)
    # When the item says it stops being true ("the scratch org expires on 28 Oct"): stored as valid_to,
    # and recall stops serving it as current after that moment.
    valid_until: datetime | None = None


@dataclass
class ExtractionResult:
    items: list[Candidate]
    dropped_no_quote: int = 0
    dropped_invalid: int = 0
    dropped_gate: int = 0  # progress events and status snapshots the knowledge gate refused
    raw: str = ""
    notes: list[str] = field(default_factory=list)


_BAD_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')


def _loads(text: str):
    """json.loads that accepts raw control characters in strings and repairs invalid escapes
    (a backslash before anything JSON does not escape, as in Windows paths or regexes)."""
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        return json.loads(_BAD_ESCAPE.sub(r"\\\\", text), strict=False)


def _salvage_array(text: str) -> list | None:
    """The elements of a JSON array that parse, one by one, when the whole does not: one
    malformed element (or a truncated tail) costs that element, not the episode."""
    text = _BAD_ESCAPE.sub(r"\\\\", text)
    start = text.find("[")
    if start == -1:
        return None
    decoder = json.JSONDecoder(strict=False)
    items, i = [], start + 1
    while (j := text.find("{", i)) != -1:
        try:
            obj, i = decoder.raw_decode(text, j)
            items.append(obj)
        except json.JSONDecodeError:
            i = j + 1  # skip the broken element; a nested "{" inside it is tried and fails the same way
    return items or None


def parse_json(text: str):
    """Pull a JSON value out of model output that may be wrapped in prose or a code fence,
    tolerating invalid escapes, raw newlines in strings, and a malformed element in an array."""
    text = text.strip()
    fence = re.match(r"```(?:json)?\s*(.*?)```\s*$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return _loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost bracket pair.
    for open_, close in (("[", "]"), ("{", "}")):
        start, end = text.find(open_), text.rfind(close)
        if start != -1 and end > start:
            try:
                return _loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    salvaged = _salvage_array(text)
    if salvaged is not None:
        return salvaged
    raise ValueError(f"no JSON found in model output: {text[:2000]!r}")


_WS = re.compile(r"\s+")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def normalize(text: str) -> str:
    return _WS.sub(" ", text.translate(_QUOTES)).strip().lower()


def quote_in_source(quote: str, source: str) -> bool:
    q = normalize(quote)
    return len(q) >= 5 and q in normalize(source)


def normalize_subject(subject: str) -> str:
    return _WS.sub(" ", str(subject)).strip().lower()[:80]


STANDING_WORDS = re.compile(r"\b(always|whenever|every time|each time|never|from now on|going forward)\b", re.IGNORECASE)


def is_standing(flag, kind: str, statement: str) -> bool:
    """The model's flag when it gave one; otherwise a preference or procedure phrased as a rule."""
    if isinstance(flag, bool):
        return flag and kind in {"preference", "procedural"}
    return kind in {"preference", "procedural"} and bool(STANDING_WORDS.search(statement))


def parse_date(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def end_date(value, valid_from: datetime | None) -> datetime | None:
    """valid_until as the moment the item stops being true: a bare date means the last second of that
    day (UTC), so "expires on 28 Oct" is still current on 28 Oct and reads "until 28 Oct". None when
    missing, unparseable, or not after valid_from (an "end" before the start is a misread start)."""
    end = parse_date(value)
    if end is None:
        return None
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    if isinstance(value, str) and len(value.strip()) == 10:  # YYYY-MM-DD
        end += timedelta(days=1, seconds=-1)
    start = valid_from.replace(tzinfo=valid_from.tzinfo or UTC) if valid_from else None
    if start is not None and end <= start:
        return None
    return end


def candidates_from(raw, source: str, custom: frozenset[str] = frozenset(), *, claims: bool = True) -> ExtractionResult:
    """`claims`: downgrade completion claims (CLAIM_WORDS) to 'claimed'; off for a conversation, where
    "the user said all tests pass" is a detail to remember, not an agent's self-report."""
    result = ExtractionResult(items=[], raw=raw if isinstance(raw, str) else "")
    data = parse_json(raw) if isinstance(raw, str) else raw
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        data = data["items"]
    if not isinstance(data, list):
        raise ValueError("extraction output is not a list")

    seen: set[str] = set()
    for entry in data:
        if not isinstance(entry, dict):
            result.dropped_invalid += 1
            continue
        statement = str(entry.get("statement", "")).strip()
        quote = str(entry.get("quote", "")).strip()
        kind = str(entry.get("kind", "")).strip()
        assertion = str(entry.get("assertion", "")).strip()
        if not statement or kind not in KINDS or assertion not in ASSERTIONS:
            result.dropped_invalid += 1
            continue
        if not quote_in_source(quote, source):
            result.dropped_no_quote += 1
            continue
        key = normalize(statement)
        if key in seen:
            continue
        seen.add(key)

        if claims and CLAIM_WORDS.search(statement) and assertion != "observed":
            assertion = "claimed"
        confidence = str(entry.get("confidence", "medium"))
        if confidence not in CONFIDENCES:
            confidence = "medium"
        if assertion == "claimed" and confidence == "high":
            confidence = "medium"
        subjects = [normalize_subject(s) for s in (entry.get("subjects") or []) if str(s).strip()][:3]

        category = str(entry.get("category") or "").strip().lower()
        why = str(entry.get("why") or "").strip()
        thread_key = str(entry.get("thread_key") or "").strip()
        scope = str(entry.get("scope") or "").strip().lower()
        result.items.append(
            Candidate(
                statement=statement,
                kind=kind,
                subjects=subjects,
                assertion=assertion,
                confidence=confidence,
                valid_from=parse_date(entry.get("valid_from")),
                valid_until=end_date(entry.get("valid_until"), parse_date(entry.get("valid_from"))),
                quote=quote,
                standing=is_standing(entry.get("standing"), kind, statement),
                category=category if category in CATEGORY_KIND or category in custom else None,
                why=why[:300] or None,
                thread_key=thread_key[:200] or None,
                scope=scope if scope in SCOPES else None,
            )
        )
    return result


# Each knowledge category is stored under the memory kind it behaves like, so reconciliation,
# staleness anchors and recall treat it accordingly.
CATEGORY_KIND = {"decision": "semantic", "fact": "semantic", "rule": "procedural", "gotcha": "procedural",
                 "preference": "preference"}
# What the knowledge prompt is told to refuse but a model sometimes keeps anyway: a step of the
# work ("cherry-picked 3fa91c0b7", "staging dry-run validate passed 55/55") or a dated status snapshot ("as of
# 21 Oct still waiting for SHAs"). True a moment, useless to a teammate a week later.
PROGRESS = re.compile(
    r"\bcherry-?pick(ed|s)?\b|\bpass(ed|es|ing)?\s+\d+\s*/\s*\d+|\b\d+\s*/\s*\d+\s+(tests?\s+|checks?\s+)?pass"
    r"|\bas of (\d{1,2}\s+[a-z]{3,9}|today|now|this morning)\b|\bstill (waiting|pending)\b",
    re.IGNORECASE,
)
# Run-specific trivia the knowledge prompt is told to refuse, now that coding sessions send tool
# outcome lines: one run's temp or scratch paths, the summary line itself, ids that name one
# session or job, and completion status. True for that run, noise for everyone after it.
TRIVIA = re.compile(
    # A rule about temp folders ("tests must not write to /tmp/") survives; a path inside one run's
    # temp folder (/tmp/claude-501/..., /tmp/tmpk3j9x/...) does not.
    r"<tmp>|/private/tmp/|/var/folders/|(?<![\w.])/tmp/[\w.-]*\d|\bscratchpad\b|\[tools\]"
    r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
    r"|\b(?:the\s+)?(?:task|implementation|work|job)\s+(?:is\s+)?(?:now\s+)?(?:complete|completed|finished|done)\b",
    re.IGNORECASE,
)


def gated(statement: str) -> bool:
    """True for a statement the knowledge gate refuses: a progress event, a status snapshot,
    or run-specific trivia."""
    return bool(PROGRESS.search(statement) or TRIVIA.search(statement))


# A project's own categories (project_categories) describe documents of knowledge, stored as semantic.
CUSTOM_KIND = "semantic"
_SLUGISH = re.compile(r"[\s_/]+")


def resolve_category(value, custom: dict[str, str] | None = None) -> str | None:
    """The category to store for what the model gave: a built-in, or one of the project's own
    categories (`custom` maps slug and lowercased name to slug; "Runbook step", "runbook_step" find
    "runbook-step"). Anything else, when the project has categories of its own, is the model reaching
    for one that does not exist: it falls back to the built-in "fact". Without custom categories an
    unknown category is None (invalid), as it was: a model writing "progress" is saying it is not
    knowledge."""
    category = str(value or "").strip().lower()
    if category in CATEGORY_KIND:
        return category
    if not custom or not category:
        return None
    return custom.get(category) or custom.get(_SLUGISH.sub("-", category)) or "fact"


def custom_lookup(categories: list[tuple[str, str, str]] | None) -> dict[str, str]:
    """slug and lowercased name -> slug, for resolve_category."""
    out: dict[str, str] = {}
    for slug, name, _ in categories or []:
        out[slug] = slug
        out.setdefault(name.strip().lower(), slug)
        out.setdefault(_SLUGISH.sub("-", name.strip().lower()), slug)
    return out


def knowledge_from(raw, source: str, categories: list[tuple[str, str, str]] | None = None) -> ExtractionResult:
    """Knowledge candidates: a verbatim quote, claims downgraded, and the gate:
    a known category, and no progress event, status snapshot or run-specific trivia (gated).
    `categories`: the project's own (slug, name, description), accepted besides the built-ins."""
    data = parse_json(raw) if isinstance(raw, str) else raw
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        data = data["items"]
    if not isinstance(data, list):
        raise ValueError("knowledge output is not a list")
    custom = custom_lookup(categories)
    kept, invalid, refused = [], 0, 0
    for entry in data:
        category = resolve_category(entry.get("category"), custom) if isinstance(entry, dict) else None
        if category is None:
            invalid += 1
            continue
        if gated(str(entry.get("statement", ""))):
            refused += 1
            continue
        kept.append({**entry, "category": category, "kind": CATEGORY_KIND.get(category, CUSTOM_KIND),
                     "assertion": entry.get("assertion") or "stated"})
    result = candidates_from(kept, source, frozenset(slug for slug, _, _ in categories or []))
    result.raw = raw if isinstance(raw, str) else ""
    result.dropped_invalid += invalid
    result.dropped_gate = refused
    return result


async def extract_knowledge(llm: LLM, *, label: str, occurred_at: datetime, content: str, project: str,
                            threads: list[tuple[str, str]], context: str = "", instructions: str | None = None,
                            categories: list[tuple[str, str, str]] | None = None) -> ExtractionResult:
    """Durable knowledge from `content`, judged against the project profile. `threads`: (key, title)
    of the work this episode touched, so an item can name the one it belongs to. `instructions` and
    `categories`: the project's memory instructions and its own (slug, name, description) categories."""
    raw = await llm.complete(KNOWLEDGE_SYSTEM, knowledge_user(label, occurred_at.isoformat(), content, project, threads, context,
                                                              instructions=instructions, categories=categories),
                             max_tokens=8192)
    return knowledge_from(raw, content, categories)



# ------------------------------------------------------------------ conversation mode
# Details of a conversation (CONVERSATION_SYSTEM): the categories it may use and the memory kind each
# is stored under. "event" and "plan" exist only here.
DETAIL_KIND = {"fact": "semantic", "decision": "semantic", "preference": "preference", "rule": "procedural",
               "event": "episodic", "plan": "prospective"}


def details_from(raw, source: str) -> ExtractionResult:
    """Detail candidates: a known category and a verbatim quote. No gate: progress, states of the
    moment and one-off values are exactly what a conversation's memory is asked about later. Dates
    are the episode's own (valid_from, valid_until unset), so a plan for next month is on record from
    the day it was said and never expires by itself."""
    data = parse_json(raw) if isinstance(raw, str) else raw
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        data = data["items"]
    if not isinstance(data, list):
        raise ValueError("details output is not a list")
    kept, invalid = [], 0
    for entry in data:
        category = str(entry.get("category") or "").strip().lower() if isinstance(entry, dict) else ""
        if category not in DETAIL_KIND:
            invalid += 1
            continue
        kept.append({**entry, "category": category, "kind": DETAIL_KIND[category], "assertion": "stated",
                     "valid_from": None, "valid_until": None, "thread_key": None, "scope": "project"})
    result = candidates_from(kept, source, frozenset(DETAIL_KIND), claims=False)
    result.raw = raw if isinstance(raw, str) else ""
    result.dropped_invalid += invalid
    return result


async def extract_details(llm: LLM, *, label: str, occurred_at: datetime, content: str, project: str,
                          context: str = "", instructions: str | None = None) -> ExtractionResult:
    """Every concrete detail in `content`, for a project in conversation mode."""
    raw = await llm.complete(CONVERSATION_SYSTEM, conversation_user(label, occurred_at.isoformat(), content, project, context,
                                                                    instructions=instructions),
                             max_tokens=16000)
    return details_from(raw, content)
