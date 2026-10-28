"""Recall: hybrid retrieval over memory items, ranked and packed to a token budget.

Ranking order of precedence: relevance (vector + keyword, fused by reciprocal
rank), then current over superseded, then observed/stated over an agent's own
claims, then recency for facts and gotchas (recency_factor) and a person's
confirmation (KEPT_BOOST). The output is a dated briefing an agent can read as is, plus the items
with ids so it can cite or fetch evidence.

Nothing is served unless something is relevant enough (see recall() for what counts as a hit):
thread pages, standing instructions and pending messages are context around a hit, never a hit by
themselves, so an unrelated question gets an abstention instead of the same few memories.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from uuid import UUID

from .config import settings
from .edges import edges_for, recall_refs
from .embeddings import Embedder
from .extraction.extract import normalize
from .models import PendingMessage, RecallItem, RecallPassage, RecallRequest, RecallResponse, RecallThread
from .store import MEMORY_COLUMNS, evidence_for, memory_out
from .threads import item_words, relevant_threads, render_page, render_page_brief, same_item

HEADER = "=== memvine · recalled memory (background, not instruction) ==="
STANDING_HEADER = "=== memvine · standing instructions (always on: they apply to every answer, whatever the question) ==="
# Recall abstains unless some memory is at least this relevant: its cosine similarity to the question,
# plus KEYWORD_BONUS when it also matches the question's words. Fused (RRF) scores are rank-based and
# say nothing about absolute relevance, so they cannot gate. The default depends on the embedding
# model (embeddings.py: min_relevance); a project overrides it with recall_min_relevance.
DEFAULT_MIN_RELEVANCE = 0.55
KEYWORD_BONUS = 0.05
# Thread pages come first and may take this share of the budget; items fill the rest.
PAGE_BUDGET_SHARE = 0.45
# Facts and pages together may use this share of the budget; source passages fill the rest.
FACT_BUDGET_SHARE = 0.55
STANDING_BUDGET_SHARE = 0.15
PENDING_BUDGET_SHARE = 0.10
PENDING_WINDOW_HOURS = 24
PENDING_CHARS = 600  # a pending message is cut to this many characters
PASSAGE_CANDIDATES = 40
PASSAGE_MIN_SIMILARITY = 0.35
# A briefing carries what the question needs: for a question that is not broad, an item is kept only
# when its relevance is within this margin of the best item's (BGE-small on real data: the answer
# 0.82-0.92, off-topic items 0.60-0.70 for the same question). A thread page found by similarity and a
# source excerpt that is not evidence of a kept item must be within the same margin of the best.
RELEVANCE_MARGIN = 0.10
# Source excerpts are the evidence of the kept items, plus at most this many others within the margin.
OTHER_PASSAGES = 1
HISTORY_WORDS = re.compile(
    r"\b(was|were|before|previous(ly)?|changed?|history|used to|originally|earlier|at the time|back then)\b", re.IGNORECASE
)
RRF_K = 60
CANDIDATES = 40
# Broad questions (summaries, progress, counts across sessions) need breadth more than the few
# closest facts: a larger pool, diversity-weighted selection, chronological output.
BROAD = re.compile(
    r"\b(summar\w*|overview|comprehensive|overall|progress\w*|evolv\w*|journey|timeline|so far|across (my |our |the )?"
    r"(sessions|conversations)|everything|all the|how many|list (all|every)|throughout)\b", re.IGNORECASE)
BROAD_CANDIDATES = 200
MMR_LAMBDA = 0.25        # weight of novelty against relevance for ordinary questions
MMR_LAMBDA_BROAD = 0.6
MIN_SIMILARITY = 0.30
ASSERTION_WEIGHT = {"observed": 1.0, "stated": 1.0, "inferred": 0.8, "claimed": 0.5}
STATUS_WEIGHT = {"active": 1.0, "superseded": 0.6, "stale": 0.7}
# Recency: among comparably relevant memories, a fact or gotcha said recently ranks above one last said
# long ago. Its score is multiplied by RECENCY_FLOOR + (1 - RECENCY_FLOOR) * 0.5 ** (age / half-life),
# age in days since it was last said (the later of recorded_at and its newest evidence episode). The
# floor keeps an old fact that is clearly the best match on top. Decisions, rules and preferences never
# decay: they hold until superseded. The half-life is the project's recall_half_life_days, else
# settings.recall_half_life_days (90); 0 turns recency off. Ranking only: the relevance gate and the
# trim margin use raw relevance, and a history question (include_history, as_of) gets no recency.
RECENCY_FLOOR = 0.6
DECAYING_CATEGORIES = frozenset({"fact", "gotcha"})
DECAYING_KINDS = frozenset({"semantic", "episodic"})  # without a category: plain facts and events
# A memory a person confirmed (review 'kept': Keep in the console, or written by a person) ranks a little
# higher than an equally relevant one nobody looked at.
KEPT_BOOST = 1.1
# Contradiction pairs listed first: among this many top-ranked facts, at most this many pairs.
CONFLICT_SCAN = 12
# Expired: an item that said when it stops being true ("the scratch org expires on 28 Oct", stored as
# valid_to) keeps its status ('active' or 'stale') past that moment; this clause is what keeps it out of
# a briefing about now. A historical question (include_history) or an as_of before the end still sees it.
NOT_EXPIRED = "(valid_to IS NULL OR valid_to > now())"
MAX_CONFLICT_PAIRS = 3


def tokens_of(text: str) -> int:
    return max(1, len(text) // 4)


def _day(dt: datetime) -> str:
    return dt.strftime("%-d %b %Y")


def _short(dt: datetime) -> str:
    """A date as "21 Oct", with the year only when it is not this year's."""
    return dt.strftime("%-d %b") if dt.year == datetime.now(dt.tzinfo).year else _day(dt)


# What an item is when it has no category (a fact stored directly without one).
KIND_WORD = {"semantic": "fact", "procedural": "procedure", "episodic": "event", "prospective": "plan", "preference": "preference"}
# Trust words (docs/how-memory-works.md), shown where trust is lower than a person saying it or a run showing it.
TRUST_WORD = {"claimed": "assistant said, unverified", "inferred": "inferred"}
NEIGHBOR_CHARS = 120


def _clip(text: str, limit: int = NEIGHBOR_CHARS) -> str:
    text = text.rstrip(".")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def render_line(row, previous: str | None, conflicts: list[tuple[str, datetime]] | None = None,
                links: dict | None = None) -> str:
    """One fact as an agent reads it: what it is, when, how far to trust it and its edges, in the
    graph's own words; then the statement, and the neighbours it needs to be read right:

        [decision 15 Oct, supersedes 6 Oct] The launch moved to 14 Nov (was: The launch is 31 Oct)
        [gotcha 3 Oct, anchored src/queue.py, MAY BE OUTDATED] Retries cap at 3 (src/queue.py changed since 3 Oct 2026; check the code)
        [fact 20 Oct, inferred, derived from 3 memories] Priya owns partner onboarding

    `previous` is the statement this one superseded; `conflicts` the contradicting statements to
    quote (a pair the briefing already lists above is only named in the header); `links` the
    other edges, from line_links."""
    links = links or {}
    head = [f"{row.get('category') or KIND_WORD.get(row['kind'], 'fact')} {_short(row['recorded_at'])}"]
    if row["assertion"] in TRUST_WORD:
        head.append(TRUST_WORD[row["assertion"]])
    if row["status"] == "superseded":
        head.append(f"superseded {_short(row['valid_to'])}" if row.get("valid_to") else "superseded")
    elif row.get("valid_to") and row["status"] != "retired":
        # A time-bound item: "ended" once past (only a historical question sees it then), else "until".
        ended = row["valid_to"] <= datetime.now(UTC)
        head.append(f"{'ended' if ended else 'until'} {_short(row['valid_to'])}")
    if previous:
        head.append(f"supersedes {_short(links['supersedes'])}" if links.get("supersedes") else "supersedes")
    head += [f"contradicts {_short(at)}" for at in links.get("contradicts", [])]
    head += [f"extends {_short(at)}" for _, at in links.get("extends", [])]
    if links.get("derived"):
        head.append(f"derived from {links['derived']} memories")
    files = links.get("anchored", [])
    if files:
        head.append(f"anchored {files[0]}" + (f" +{len(files) - 1}" if len(files) > 1 else ""))
    stale = row["status"] == "stale" and row.get("stale_reason")
    if stale:
        head.append("MAY BE OUTDATED")
    parts = [f"[{', '.join(head)}] {row['statement'].rstrip('.')}"]
    if previous:
        parts.append(f" (was: {previous.rstrip('.')})")
    for other, _ in conflicts or []:
        parts.append(f" (other side: {other.rstrip('.')})")
    for other, _ in links.get("extends", []):
        parts.append(f" (adds to: {_clip(other)})")
    if stale:
        parts.append(f" ({row['stale_reason']}; check the code)")
    return "".join(parts)


def line_links(edges: list[dict], neighbors: dict, *, supersedes_at: datetime | None = None,
               contradicts_at: list[datetime] | None = None) -> dict:
    """What a briefing line names besides its statement, from edges.edges_for: the superseded
    fact's date, the contradicting facts' dates, the older facts this one extends, how many
    memories it was derived from, and the files it is anchored to."""
    out: dict = {"supersedes": supersedes_at, "contradicts": list(contradicts_at or []), "extends": [], "derived": 0,
                 "anchored": []}
    for e in edges:
        if e["direction"] != "out":
            continue
        if e["kind"] == "extends" and e.get("other") in neighbors:
            n = neighbors[e["other"]]
            out["extends"].append((n["statement"], n["recorded_at"]))
        elif e["kind"] == "derived":
            out["derived"] += 1
        elif e["kind"] == "anchored" and e["file"]["path"] not in out["anchored"]:
            out["anchored"].append(e["file"]["path"])
    return out


async def recall(conn, embedder: Embedder, project_id: UUID, req: RecallRequest, audiences: list[str] | None = None,
                 *, track: bool = True) -> RecallResponse:
    """audiences: the ones this recall may read (from access.readable_audiences); None reads all.
    track: record what was served (mark_recalled), so unused memory can surface for review; the
    console's own playground passes False.

    Abstention: something must be a hit for anything to be served. A hit is a memory whose relevance
    (cosine similarity to the question, plus KEYWORD_BONUS when it also matches the question's words)
    reaches the threshold; the thread page of the caller's git branch or a page the question names
    by key; or a pending message sharing a word with the question. Pages found only by
    similarity, standing instructions, and unrelated pending messages are context, never hits. On
    abstention the briefing is empty, but the standing instructions still come back in `standing`."""
    budget = min(req.budget or settings.recall_default_budget, settings.recall_max_budget)
    include_history = req.include_history if req.include_history is not None else bool(HISTORY_WORDS.search(req.query))
    if req.as_of:
        include_history = True
    statuses = ["active", "stale"] + (["superseded"] if include_history else [])

    time_clause, time_params = "", []
    if req.as_of:
        time_clause = "AND valid_from <= %s AND (valid_to IS NULL OR valid_to > %s)"
        time_params = [req.as_of, req.as_of]
    elif not include_history:
        time_clause = f"AND {NOT_EXPIRED}"
    if audiences is not None:
        time_clause += " AND audience = ANY(%s)"
        time_params = [*time_params, audiences]

    broad = bool(BROAD.search(req.query))
    pool = BROAD_CANDIDATES if broad else CANDIDATES
    embedding = (await embedder.embed([req.query]))[0]
    project_visible = audiences is None or "project" in audiences
    project = await (await conn.execute(
        "SELECT recall_min_relevance, recall_half_life_days FROM projects WHERE id = %s", (project_id,))).fetchone()
    min_relevance = min_relevance_for(project, embedder)
    half_life = 0.0 if include_history else half_life_for(project)
    threads = []
    if project_visible:
        threads = await relevant_threads(conn, project_id, req.query, embedding, session_id=req.session_id, as_of=req.as_of,
                                         branch=req.branch)
        # A page found only by similarity must be as relevant as a memory would have to be.
        threads = [t for t in threads if thread_is_hit(t) or thread_similarity(t) >= min_relevance]
    base = f"FROM memory_items WHERE project_id = %s AND status = ANY(%s) {time_clause} AND scope IS DISTINCT FROM 'thread'"

    vector_rows = await (
        await conn.execute(
            f"""
            SELECT {MEMORY_COLUMNS}, embedding, 1 - (embedding <=> %s::vector) AS similarity, 0::float AS rank
            {base} AND embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector LIMIT %s
            """,
            [embedding, project_id, statuses, *time_params, embedding, pool],
        )
    ).fetchall()
    # Any-term match, ranked by how many terms hit: a question rarely contains
    # every word of the fact that answers it, so the AND semantics of
    # websearch_to_tsquery would miss most of them.
    or_query = "replace(plainto_tsquery('english', %s)::text, '&', '|')::tsquery"
    keyword_rows = await (
        await conn.execute(
            f"""
            SELECT {MEMORY_COLUMNS}, embedding, coalesce(1 - (embedding <=> %s::vector), 0)::float AS similarity,
                   ts_rank(tsv, {or_query}) AS rank
            {base} AND plainto_tsquery('english', %s)::text <> '' AND tsv @@ {or_query}
            ORDER BY rank DESC LIMIT %s
            """,
            [embedding, req.query, project_id, statuses, *time_params, req.query, req.query, pool],
        )
    ).fetchall()

    # Reciprocal rank fusion over the two lists, then trust and currency weights.
    scores: dict[UUID, float] = {}
    rows: dict[UUID, dict] = {}
    keyword_hit = {r["id"] for r in keyword_rows}
    for ranked in (vector_rows, keyword_rows):
        for position, r in enumerate(ranked):
            rows.setdefault(r["id"], r)
            if r["similarity"]:
                rows[r["id"]]["similarity"] = r["similarity"]
            scores[r["id"]] = scores.get(r["id"], 0.0) + 1.0 / (RRF_K + position + 1)

    def relevance(mid: UUID) -> float:
        return float(rows[mid].get("similarity") or 0) + (KEYWORD_BONUS if mid in keyword_hit else 0.0)

    excluded = set(req.exclude)
    last_said = await last_said_at(conn, [mid for mid, r in rows.items() if decays(r)]) if half_life > 0 else {}
    now = datetime.now(UTC)
    # A memory that would count as a hit is never filtered out as too dissimilar to show.
    floor = min(MIN_SIMILARITY, min_relevance)
    ranked_ids = []
    # The score before recency and the kept boost: the trim's always-kept first item is chosen by it,
    # so recency reorders what the margin keeps but never brings in what it would not.
    base_score: dict[UUID, float] = {}
    for mid, score in scores.items():
        r = rows[mid]
        if mid in excluded:
            continue
        if mid not in keyword_hit and (r.get("similarity") or 0) < floor:
            continue
        score *= ASSERTION_WEIGHT.get(r["assertion"], 0.8) * STATUS_WEIGHT.get(r["status"], 0.7)
        base_score[mid] = score
        if mid in last_said:
            said = max(r["recorded_at"], last_said[mid] or r["recorded_at"])
            score *= recency_factor((now - said).total_seconds() / 86400, half_life)
        if r.get("review") == "kept":
            score *= KEPT_BOOST
        ranked_ids.append((score, mid))
    candidates = [mid for mid in rows if mid not in excluded]
    top_relevance = round(max(relevance(mid) for mid in candidates), 4) if candidates else None

    standing_rows = await standing_instructions(conn, project_id, req, audiences, int(budget * STANDING_BUDGET_SHARE))
    standing = [r["statement"] for r in standing_rows]
    pending = await pending_messages(conn, project_id, req, audiences, int(budget * PENDING_BUDGET_SHARE))
    terms = query_terms(req.query)
    hit = (any(relevance(mid) >= min_relevance for _, mid in ranked_ids)
           or any(thread_is_hit(t) for t in threads)
           or any(shared_terms(terms, m.content) for m in pending))
    if not hit:
        if track:  # standing instructions are served with every answer, abstention or not
            await mark_recalled(conn, [r["id"] for r in standing_rows])
        if candidates:
            reason = "below_threshold"
        elif await has_memory(conn, project_id, audiences):
            reason = "no_candidates"
        else:
            reason = "empty_store"
        return RecallResponse(briefing="", standing=standing, items=[], tokens=0, abstained=True,
                              abstain_reason=reason, min_relevance=min_relevance, top_relevance=top_relevance)

    ranked_ids.sort(key=lambda t: (-t[0], -rows[t[1]]["recorded_at"].timestamp()))
    ranked_ids = diversify(ranked_ids, rows, MMR_LAMBDA_BROAD if broad else MMR_LAMBDA)

    # Standing rules are said in their own section, never again as an item.
    standing_ids = {r["id"] for r in standing_rows}
    standing_words = [item_words(s) for s in standing]
    ranked_ids = [(s, mid) for s, mid in ranked_ids
                  if mid not in standing_ids and not said_in(item_words(rows[mid]["statement"]), standing_words)]
    # What the question needs: items within the margin of the best one (with their contradiction and
    # supersede partners); pages by similarity only within the same margin. Broad questions keep breadth.
    best = max((relevance(mid) for _, mid in ranked_ids), default=None)
    if not broad:
        first = max(ranked_ids, key=lambda t: (base_score[t[1]], rows[t[1]]["recorded_at"]))[1] if ranked_ids else None
        ranked_ids = trim_to_margin(ranked_ids, rows, relevance, first=first)
        similar = [thread_similarity(t) for t in threads if not thread_is_hit(t)]
        best_any = max([*([best] if best is not None else []), *similar], default=0.0)
        threads = [t for t in threads if thread_is_hit(t) or thread_similarity(t) >= best_any - RELEVANCE_MARGIN]

    # A page says only what the rest of the briefing does not: section items and thread knowledge
    # already said by a kept item or a standing rule (same words, or the same evidence) are left out,
    # and a page found by similarity with nothing left is not served.
    kept_ids = [mid for _, mid in ranked_ids]
    said_evidence = await evidence_for(conn, [*kept_ids, *standing_ids])
    said_words = standing_words + [item_words(rows[mid]["statement"]) for mid in kept_ids]
    said_marks = {(str(e.episode_id), _quote(e.quote)) for ev in said_evidence.values() for e in ev}
    # Outdated: the values the kept items replaced, and any superseded value drawn from an episode a
    # candidate page cites. A page line stating one is never served.
    outdated = await outdated_values(conn, project_id, [rows[mid]["supersedes"] for mid in kept_ids if rows[mid]["supersedes"]],
                                     list({ep for t in threads for ep in cited_episodes(t)}))
    said_words += [item_words(o["statement"]) for o in outdated]
    said_marks |= {(str(o["episode_id"]), _quote(o["quote"])) for o in outdated if o["episode_id"]}
    # A page found only by similarity is compared by source episode too: a line citing the episode a
    # kept or outdated item came from restates it. The caller's branch page and a named page are
    # compared by wording and quote only, so a long session's other work stays on them.
    said_episodes = {str(e.episode_id) for mid in kept_ids for e in said_evidence.get(mid, [])}
    said_episodes |= {str(o["episode_id"]) for o in outdated if o["episode_id"]}
    # Knowledge scoped to one thread is served only beside that thread's page.
    by_thread = await thread_knowledge(conn, [t["id"] for t in threads], time_clause, time_params) if threads else {}
    trimmed_threads = []
    for thread in threads:
        hit_page = thread_is_hit(thread)
        if not hit_page and about_outdated(thread, outdated):
            continue
        sections, knowledge = unsaid(thread, by_thread.get(thread["id"], []), said_words, said_marks,
                                     set() if hit_page else said_episodes)
        if not sections and not knowledge and not hit_page:
            continue
        by_thread[thread["id"]] = knowledge
        trimmed_threads.append({**thread, "sections": sections})
    threads = trimmed_threads

    # Every line a briefing may carry: the ranked facts, and the thread knowledge under the pages.
    knowledge_rows = [k for t in threads for k in by_thread.get(t["id"], [])]
    line_rows = [rows[mid] for _, mid in ranked_ids] + knowledge_rows

    # Previous values, for "(was: ...)" on updated facts, and when they were said.
    prev_ids = [r["supersedes"] for r in line_rows if r["supersedes"]]
    previous: dict[UUID, str] = {}
    previous_at: dict[UUID, datetime] = {}
    if prev_ids:
        for pr in await (
            await conn.execute("SELECT id, statement, recorded_at FROM memory_items WHERE id = ANY(%s)", (prev_ids,))
        ).fetchall():
            previous[pr["id"]] = pr["statement"]
            previous_at[pr["id"]] = pr["recorded_at"]
    # The other edges a line names (extends, derived, anchored), read once for every candidate line.
    line_edges, line_neighbors, _ = await edges_for(conn, project_id, list({r["id"] for r in line_rows}), audiences)

    # Active items that contradict a chosen one are shown beside it, never hidden.
    conflict_ids = {c for r in line_rows for c in (r.get("conflicts_with") or [])}
    conflict_text: dict[UUID, tuple[str, datetime]] = {}
    if conflict_ids:
        for cr in await (
            await conn.execute(
                f"SELECT id, statement, recorded_at FROM memory_items WHERE id = ANY(%s) AND status = 'active' AND {NOT_EXPIRED}",
                (list(conflict_ids),)
            )
        ).fetchall():
            conflict_text[cr["id"]] = (cr["statement"], cr["recorded_at"])

    def conflicts_of(r) -> list[tuple[str, datetime]]:
        return [conflict_text[c] for c in (r.get("conflicts_with") or []) if c in conflict_text]

    # Standing instructions are always-on context in a section of their own, ahead of the recalled
    # memory: they apply whatever the question, so they are not evidence that memory matched it.
    standing_lines, spent = [], tokens_of(HEADER)
    if standing:
        standing_lines = [STANDING_HEADER, *(f"- {s}" for s in standing)]
        spent += sum(tokens_of(s) + 1 for s in standing) + tokens_of(STANDING_HEADER)
    shown = set(standing)

    # Contradictions among the most relevant facts come first, as pairs: an agent that meets
    # them only inside a summary tends to pick a side instead of asking which is right.
    conflict_lines, pairs, seen_pairs = [], [], set()
    for _, mid in ranked_ids[:CONFLICT_SCAN]:
        r = rows[mid]
        for other in r.get("conflicts_with") or []:
            key = frozenset((mid, other))
            if other in conflict_text and key not in seen_pairs and len(pairs) < MAX_CONFLICT_PAIRS:
                seen_pairs.add(key)
                pairs.append(((r["statement"], r["recorded_at"]), conflict_text[other]))
    if pairs:
        conflict_lines.append("Conflicting statements on record (both stand; neither replaced the other). "
                              "If the question depends on one, say both and ask which is correct:")
        spent += 30
        for (a, a_at), (b, b_at) in pairs:
            text = f"- {a.rstrip('.')} ({_day(a_at)}) vs {b.rstrip('.')} ({_day(b_at)})"
            conflict_lines.append(text)
            spent += tokens_of(text)

    def line_for(r) -> str:
        # A contradiction already paired above is named in the line's header, not quoted again.
        quoted = [conflict_text[c] for c in (r.get("conflicts_with") or [])
                  if c in conflict_text and frozenset((r["id"], c)) not in seen_pairs]
        links = line_links(line_edges.get(r["id"], []), line_neighbors, supersedes_at=previous_at.get(r["supersedes"]),
                           contradicts_at=[at for _, at in conflicts_of(r)])
        return render_line(r, previous.get(r["supersedes"]) if r["supersedes"] else None, quoted, links)
    # Thread pages are placed now, ahead of the facts.
    page_cap = int(budget * PAGE_BUDGET_SHARE)
    thread_lines, shown_threads, served_knowledge = [], [], []
    for thread in threads:
        # The whole page with its thread knowledge when it fits, else without, else the sections that
        # steer the next action, else heading and summary.
        knowledge = by_thread.get(thread["id"], [])
        page = render_page(thread)
        with_knowledge = "\n".join([page, "Known in this thread:", *(f"- {line_for(k)}" for k in knowledge)])
        options = [(with_knowledge, knowledge)] if knowledge else []
        for block, served in [*options, (page, []), (render_page_brief(thread), []), (render_page({**thread, "sections": []}), [])]:
            if spent + tokens_of(block) <= page_cap:
                spent += tokens_of(block)
                thread_lines.append(block)
                shown_threads.append(thread)
                served_knowledge += served
                break
    fact_cap = int(budget * FACT_BUDGET_SHARE)
    packed: list[tuple[float, dict, str | None]] = []
    for score, mid in ranked_ids:
        r = rows[mid]
        if r["statement"] in shown:
            continue
        line = line_for(r)
        cost = tokens_of(line)
        if spent + cost > fact_cap:
            if packed:
                break
            continue  # the first line alone is over budget: try a shorter one
        spent += cost
        packed.append((score, r, line))
    # A superseded value whose replacement is served is already said there as "(was: ...)": it stays
    # among the items (and its evidence among the excerpts) without a line of its own, unless that line
    # carries an older value of its own (a chain of changes keeps every step).
    replaced = {r["supersedes"] for _, r, _ in packed if r["supersedes"]}
    for i, (score, r, line) in enumerate(packed):
        if r["id"] in replaced and r["status"] == "superseded" and not r["supersedes"]:
            spent -= tokens_of(line)
            packed[i] = (score, r, None)

    pending_lines = []
    if pending:
        pending_lines.append("--- recent messages from other sessions, not yet in memory ---")
        spent += 12
        for m in pending:
            text = f"[{_day(m.at)} {m.at.strftime('%H:%M')} · {m.role}] {m.content}"
            pending_lines.append(text)
            spent += tokens_of(text)

    if broad:
        # Selected for breadth by relevance; told in time order so the answer can follow the story.
        packed.sort(key=lambda p: p[1]["valid_from"])
    fact_lines = [line for _, _, line in packed if line]
    chosen = [(score, r) for score, r, _ in packed]
    evidence = {r["id"]: said_evidence.get(r["id"], []) for _, r in chosen}
    sources = [(e.episode_id, e.quote) for _, r in chosen for e in evidence[r["id"]]]
    excerpts = await relevant_passages(conn, project_id, req, embedding, audiences, sources, budget - spent,
                                       best=best, min_relevance=min_relevance, broad=broad, include_history=include_history)
    excerpt_lines = []
    if excerpts:
        excerpt_lines.append("--- source excerpts (the original words) ---")
        spent += 10
        for p in excerpts:
            excerpt_lines.append(f"[{_day(p['occurred_at'])}] {p['content']}")
            spent += tokens_of(p["content"]) + 4

    items = [
        RecallItem(
            memory=memory_out(r, evidence[r["id"]]),
            score=round(score, 5),
            previous=previous.get(r["supersedes"]) if r["supersedes"] else None,
            conflicts=[text for text, _ in conflicts_of(r)],
        )
        for score, r in chosen
    ]
    if served_knowledge:  # shown under their pages, listed after the ranked items
        knowledge_evidence = await evidence_for(conn, [r["id"] for r in served_knowledge])
        items += [RecallItem(memory=memory_out(r, knowledge_evidence[r["id"]]), score=0.0) for r in served_knowledge]
    if req.include and req.include.related and items:
        # Edges ride along with the items, outside the briefing: they never displace a fact from the
        # packing, but what they cost is counted.
        refs = await recall_refs(conn, project_id, [i.memory.id for i in items], audiences)
        for item in items:
            item.related = refs.get(item.memory.id, [])
            spent += sum(tokens_of(ref.statement) + 2 for ref in item.related)
    if track:
        await mark_recalled(conn, [*(r["id"] for r in standing_rows), *(i.memory.id for i in items)])
    served_threads = [RecallThread(id=t["id"], key=t["key"], title=t["title"], status=t["status"], summary=t["summary"])
                      for t in shown_threads]
    served_passages = [RecallPassage(episode_id=p["episode_id"], occurred_at=p["occurred_at"], content=p["content"]) for p in excerpts]
    lines = [*standing_lines, HEADER, *conflict_lines, *thread_lines, *fact_lines,
             *pending_lines, *excerpt_lines]
    return RecallResponse(briefing="\n".join(lines), standing=standing, pending=pending, threads=served_threads,
                          passages=served_passages, items=items, tokens=spent, abstained=False,
                          min_relevance=min_relevance, top_relevance=top_relevance)


async def mark_recalled(conn, memory_ids: list[UUID]) -> None:
    """One statement for everything a briefing served: when it was last recalled, and how often."""
    if memory_ids:
        await conn.execute(
            "UPDATE memory_items SET last_recalled_at = now(), recall_count = recall_count + 1 WHERE id = ANY(%s)",
            (list(dict.fromkeys(memory_ids)),))


def min_relevance_for(project, embedder: Embedder) -> float:
    """The project's own threshold, else the server's, else the embedding model's calibrated default:
    cosine scales differ between models, so one number cannot serve every embedder."""
    if project and project.get("recall_min_relevance") is not None:
        return round(float(project["recall_min_relevance"]), 4)
    if settings.recall_min_relevance is not None:
        return settings.recall_min_relevance
    return getattr(embedder, "min_relevance", DEFAULT_MIN_RELEVANCE)


def half_life_for(project) -> float:
    """The project's recency half-life in days, else the server's; 0 means no recency weighting."""
    if project and project.get("recall_half_life_days") is not None:
        return float(project["recall_half_life_days"])
    return float(settings.recall_half_life_days or 0)


def decays(row) -> bool:
    """Whether recency weighs on a memory: facts and gotchas (and, without a category, plain facts and
    events) do; decisions, rules, preferences and standing rules hold until superseded."""
    if row.get("standing"):
        return False
    category = row.get("category")
    return category in DECAYING_CATEGORIES if category else row.get("kind") in DECAYING_KINDS


def recency_factor(age_days: float, half_life_days: float, floor: float = RECENCY_FLOOR) -> float:
    """1.0 for something said now, halving toward `floor` every `half_life_days`; 1.0 when off."""
    if half_life_days <= 0:
        return 1.0
    return floor + (1 - floor) * 0.5 ** (max(age_days, 0.0) / half_life_days)


async def last_said_at(conn, memory_ids: list[UUID]) -> dict[UUID, datetime | None]:
    """When each memory was last said: its newest evidence episode (a restatement adds evidence),
    None when it has none. Every given id is a key."""
    out: dict[UUID, datetime | None] = dict.fromkeys(memory_ids)
    if memory_ids:
        for r in await (await conn.execute(
                "SELECT v.memory_id, max(e.occurred_at) AS at FROM evidence v "
                "JOIN episodes e ON e.project_id = v.project_id AND e.id = v.episode_id "
                "WHERE v.memory_id = ANY(%s) GROUP BY v.memory_id", (memory_ids,))).fetchall():
            out[r["memory_id"]] = r["at"]
    return out


def trim_to_margin(ranked: list[tuple[float, UUID]], rows: dict, relevance, margin: float = RELEVANCE_MARGIN,
                   first: UUID | None = None) -> list[tuple[float, UUID]]:
    """The ranked items within `margin` of the best relevance, in rank order. The first-ranked (or
    `first`: the first by the score before recency) and the most relevant item always stay, and so
    does every item that contradicts, supersedes or is superseded by a kept one: a pair is shown
    together or not at all."""
    if not ranked:
        return ranked
    best = max(relevance(mid) for _, mid in ranked)
    keep = {first if first is not None else ranked[0][1], max(ranked, key=lambda t: relevance(t[1]))[1]}
    keep |= {mid for _, mid in ranked if relevance(mid) >= best - margin}
    present = {mid for _, mid in ranked}
    todo = list(keep)
    while todo:
        r = rows[todo.pop()]
        for other in [*(r.get("conflicts_with") or []), r.get("supersedes"), r.get("superseded_by")]:
            if other in present and other not in keep:
                keep.add(other)
                todo.append(other)
    return [(s, mid) for s, mid in ranked if mid in keep]


def said_in(words: frozenset[str], said: list[frozenset[str]]) -> bool:
    return any(same_item(words, w) for w in said)


def _quote(text: str | None) -> str:
    return normalize(text or "").rstrip(" .")


def _sections(thread: dict) -> list[dict]:
    sections = thread.get("sections")
    return (json.loads(sections) if isinstance(sections, str) else sections) or []


def cited_episodes(thread: dict) -> set[str]:
    return {str(ev["episode_id"]) for sec in _sections(thread) for i in sec.get("items") or []
            for ev in i.get("evidence") or [] if ev.get("episode_id")}


async def outdated_values(conn, project_id: UUID, replaced_ids: list[UUID], episodes: list[str]) -> list[dict]:
    """Superseded items that are either replaced by a kept item or drawn from one of `episodes`,
    one row per evidence quote, with the statement that replaced them (None if unknown)."""
    if not replaced_ids and not episodes:
        return []
    return await (await conn.execute(
        "SELECT m.id, m.statement, n.statement AS replacement, v.episode_id, v.quote FROM memory_items m "
        "LEFT JOIN memory_items n ON n.id = m.superseded_by LEFT JOIN evidence v ON v.memory_id = m.id "
        "WHERE m.project_id = %s AND m.status = 'superseded' AND (m.id = ANY(%s) OR v.episode_id = ANY(%s))",
        (project_id, replaced_ids, episodes))).fetchall()


def about_outdated(thread: dict, outdated: list[dict]) -> bool:
    """Whether a page's title and summary are about a replaced value: they name what only the old
    statement says ("port 5544") and nothing only its replacement says ("6432")."""
    words = item_words(f"{thread.get('title') or ''}. {thread.get('summary') or ''}")
    for o in outdated:
        if not o["replacement"]:
            continue
        old, new = item_words(o["statement"]), item_words(o["replacement"])
        if words & (old - new) and not words & (new - old):
            return True
    return False


def unsaid(thread: dict, knowledge: list[dict], said_words: list[frozenset[str]],
           said_marks: set[tuple[str, str]], said_episodes: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    """A page's sections and thread knowledge without what the briefing already says (or what is
    outdated): an item is said when its words match a said statement (threads.same_item), when it
    cites a quote that a said statement's evidence also cites (same episode, one quote containing
    the other), or when it cites any of `said_episodes`."""
    said_episodes = said_episodes or set()

    def quoted(ev: dict) -> bool:
        q, episode = _quote(ev.get("quote")), str(ev.get("episode_id"))
        if episode in said_episodes:
            return True
        return bool(q) and any(e == episode and s and (q in s or s in q) for e, s in said_marks)

    out = []
    for sec in _sections(thread):
        items = [i for i in sec.get("items") or []
                 if not said_in(item_words(i.get("text", "")), said_words) and not any(quoted(ev) for ev in i.get("evidence") or [])]
        if items:
            out.append({**sec, "items": items})
    return out, [k for k in knowledge if not said_in(item_words(k["statement"]), said_words)]


def thread_is_hit(thread: dict) -> bool:
    """A thread page counts as a hit only when it is the caller's branch page or the question names it
    by key or alias; a page that is merely similar is background."""
    return bool(thread.get("branch_match") or thread.get("named"))


def thread_similarity(thread: dict) -> float:
    return float(thread.get("similarity") or 0) + (0.15 if thread.get("mine") else 0.0)


STOPWORDS = frozenset(
    "about above after again also another because been before being below between both could does doing down during each "
    "from further have having here how into just more most much must other over same should some such than that their "
    "theirs them then there these they this those through under until very want were what when where which while who "
    "whom whose will with would your yours".split())


def query_terms(query: str) -> set[str]:
    """The question's content words (four letters or more, not stopwords), for plain-text matching."""
    return {w for w in re.findall(r"[a-z0-9]{4,}", query.lower()) if w not in STOPWORDS}


def shared_terms(terms: set[str], text: str) -> int:
    text = text.lower()
    return sum(1 for w in terms if w in text)


async def has_memory(conn, project_id: UUID, audiences: list[str] | None) -> bool:
    clause, params = "", [project_id]
    if audiences is not None:
        clause, params = " AND audience = ANY(%s)", [project_id, audiences]
    return bool(await (await conn.execute(f"SELECT 1 FROM memory_items WHERE project_id = %s{clause} LIMIT 1", params)).fetchone())


THREAD_KNOWLEDGE_PER_PAGE = 6


async def thread_knowledge(conn, thread_ids: list[UUID], time_clause: str, time_params: list) -> dict[UUID, list[dict]]:
    """Current thread-scoped knowledge of the given threads, newest first, a few per thread: it
    matters only inside that work, so it rides along with the page instead of competing for
    the project-wide list."""
    rows = await (await conn.execute(
        f"SELECT {MEMORY_COLUMNS} FROM memory_items WHERE thread_id = ANY(%s) AND scope = 'thread' "
        f"AND status IN ('active', 'stale') {time_clause} ORDER BY recorded_at DESC", [thread_ids, *time_params])).fetchall()
    out: dict[UUID, list[dict]] = {}
    for r in rows:
        if len(out.setdefault(r["thread_id"], [])) < THREAD_KNOWLEDGE_PER_PAGE:
            out[r["thread_id"]].append(r)
    return out


def diversify(ranked: list[tuple[float, UUID]], rows: dict, lam: float) -> list[tuple[float, UUID]]:
    """Maximal marginal relevance: repeatedly take the candidate with the best relevance minus
    lam times its similarity to what is already taken. Scores are normalized to the best one, so
    lam is a share of relevance. Candidates without an embedding keep their order at the end."""
    if lam <= 0 or len(ranked) < 3:
        return ranked
    import numpy as np

    with_vec = [(s, m) for s, m in ranked if rows[m].get("embedding") is not None]
    without = [(s, m) for s, m in ranked if rows[m].get("embedding") is None]
    if not with_vec:
        return ranked
    def as_array(v):  # pgvector hands back its own Vector type, or a numpy array, or a list
        return np.asarray(v.to_numpy() if hasattr(v, "to_numpy") else v, dtype=np.float32)

    vectors = np.array([as_array(rows[m]["embedding"]) for _, m in with_vec])
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-9
    relevance = np.array([s for s, _ in with_vec]) / max(s for s, _ in with_vec)
    taken: list[int] = []
    max_sim = np.zeros(len(with_vec))
    remaining = set(range(len(with_vec)))
    while remaining:
        best = max(remaining, key=lambda i: relevance[i] - lam * max_sim[i])
        taken.append(best)
        remaining.discard(best)
        max_sim = np.maximum(max_sim, vectors @ vectors[best])
    return [with_vec[i] for i in taken] + without


async def pending_messages(conn, project_id: UUID, req: RecallRequest, audiences: list[str] | None, budget: int) -> list[PendingMessage]:
    """Messages of other recently active sessions that extraction has not committed yet, so
    something said two minutes ago in another session is not missed. Messages that share words
    with the question come first, then the most recent; sessions are project-visible."""
    if budget < 60 or (audiences is not None and "project" not in audiences):
        return []
    params: list = [project_id, PENDING_WINDOW_HOURS, req.session_id or ""]
    time_clause = ""
    if req.as_of:
        time_clause = "AND e.at <= %s"
        params.append(req.as_of)
    rows = await (await conn.execute(
        f"""
        SELECT e.session_id, e.seq, e.role, e.at, e.content FROM session_events e
        JOIN sessions s ON s.project_id = e.project_id AND s.id = e.session_id
        WHERE e.project_id = %s AND s.last_event_at > now() - make_interval(hours => %s) AND s.id <> %s
          AND e.seq > s.processed_through AND e.role IN ('user', 'assistant') {time_clause}
        ORDER BY e.at DESC LIMIT 200
        """, params)).fetchall()
    if not rows:
        return []
    terms = query_terms(req.query)
    def overlap(r) -> int:
        return shared_terms(terms, r["content"])
    ranked = sorted(rows, key=lambda r: (-overlap(r), -r["at"].timestamp()))
    picked, spent = [], 0
    for r in ranked:
        if terms and overlap(r) == 0 and picked:
            break  # past the matching messages, only the newest one or two unrelated ones are worth it
        text = r["content"] if len(r["content"]) <= PENDING_CHARS else r["content"][:PENDING_CHARS] + " …"
        cost = tokens_of(text) + 8
        if spent + cost > budget:
            continue
        spent += cost
        picked.append(PendingMessage(session_id=r["session_id"], seq=r["seq"], role=r["role"], at=r["at"], content=text))
    return sorted(picked, key=lambda m: (m.at, m.session_id, m.seq))


async def standing_instructions(conn, project_id: UUID, req: RecallRequest, audiences: list[str] | None, budget: int) -> list[dict]:
    """Every active standing rule the reader may see, valid at the moment asked, newest first,
    within the budget."""
    clauses, params = ["project_id = %s", "standing", "status = 'active'"], [project_id]
    if audiences is not None:
        clauses.append("audience = ANY(%s)")
        params.append(audiences)
    if req.as_of:
        clauses.append("recorded_at <= %s AND (valid_to IS NULL OR valid_to > %s)")
        params += [req.as_of, req.as_of]
    else:
        clauses.append(NOT_EXPIRED)
    rows = await (await conn.execute(
        f"SELECT id, statement, subjects FROM memory_items WHERE {' AND '.join(clauses)} ORDER BY recorded_at DESC LIMIT 30", params)).fetchall()
    out, spent = [], 0
    for r in rows:
        cost = tokens_of(r["statement"]) + 1
        if spent + cost > budget:
            break
        spent += cost
        out.append(r)
    return out


async def relevant_passages(conn, project_id: UUID, req: RecallRequest, embedding: list[float], audiences: list[str] | None,
                            sources: list[tuple[str, str]], budget: int, *, best: float | None, min_relevance: float,
                            broad: bool, include_history: bool) -> list[dict]:
    """Source excerpts within the budget the facts left, nothing after `as_of`, returned in
    conversation order so a reader sees events in sequence.

    First the evidence of the chosen facts (`sources`: episode and quote): the passage of that
    episode holding the quote, else its passage closest to the question. Then others, found by
    vector and any-term keyword search fused by reciprocal rank: for a broad question as many as fit,
    otherwise at most OTHER_PASSAGES within RELEVANCE_MARGIN of the best item's relevance. An other
    passage is never the source of a standing rule (said already) or, unless the question asks for
    history, of a superseded value."""
    if budget < 150:
        return []
    clauses, params = ["project_id = %s"], [project_id]
    if audiences is not None:
        clauses.append("audience = ANY(%s)")
        params.append(audiences)
    if req.as_of:
        clauses.append("occurred_at <= %s")
        params.append(req.as_of)
    where = " AND ".join(clauses)
    similarity = "coalesce(1 - (embedding <=> %s::vector), 0)::float AS similarity"
    found: dict[tuple[str, int], dict] = {}

    evidence_keys: list[tuple[str, int]] = []
    episodes = list(dict.fromkeys(episode for episode, _ in sources))
    if episodes:
        by_episode: dict[str, list[dict]] = {}
        for p in await (await conn.execute(
                f"SELECT episode_id, ord, content, occurred_at, {similarity} FROM passages "
                f"WHERE {where} AND episode_id = ANY(%s) ORDER BY episode_id, ord", [embedding, *params, episodes])).fetchall():
            by_episode.setdefault(p["episode_id"], []).append(p)
        for episode, quote in sources:
            passages = by_episode.get(episode, [])
            q = _quote(quote)
            holding = [p for p in passages if q and q in normalize(p["content"])]
            for p in holding or sorted(passages, key=lambda p: -p["similarity"])[:1]:
                key = (p["episode_id"], p["ord"])
                found.setdefault(key, p)
                if key not in evidence_keys:
                    evidence_keys.append(key)

    by_vector = await (await conn.execute(
        f"SELECT episode_id, ord, content, occurred_at, 1 - (embedding <=> %s::vector) AS similarity FROM passages "
        f"WHERE {where} AND embedding IS NOT NULL ORDER BY embedding <=> %s::vector LIMIT %s",
        [embedding, *params, embedding, PASSAGE_CANDIDATES])).fetchall()
    or_query = "replace(plainto_tsquery('english', %s)::text, '&', '|')::tsquery"
    by_words = await (await conn.execute(
        f"SELECT episode_id, ord, content, occurred_at, {similarity} FROM passages "
        f"WHERE {where} AND plainto_tsquery('english', %s)::text <> '' AND tsv @@ {or_query} "
        f"ORDER BY ts_rank(tsv, {or_query}) DESC LIMIT %s",
        [embedding, *params, req.query, req.query, req.query, PASSAGE_CANDIDATES])).fetchall()

    scores: dict[tuple[str, int], float] = {}
    relevance: dict[tuple[str, int], float] = {}
    for ranked in (by_vector, by_words):
        for position, p in enumerate(ranked):
            key = (p["episode_id"], p["ord"])
            if ranked is by_vector and p["similarity"] < PASSAGE_MIN_SIMILARITY:
                continue
            found.setdefault(key, p)
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + position + 1)
            relevance[key] = max(relevance.get(key, 0.0), float(p["similarity"] or 0) + (KEYWORD_BONUS if ranked is by_words else 0.0))

    others = [key for key, _ in sorted(scores.items(), key=lambda kv: -kv[1]) if key not in evidence_keys]
    if others:
        # Quotes behind what an other passage must not repeat: standing rules, and replaced values.
        said = await (await conn.execute(
            "SELECT v.episode_id, v.quote FROM evidence v JOIN memory_items m ON m.id = v.memory_id "
            "WHERE v.project_id = %s AND v.episode_id = ANY(%s) "
            "AND ((m.standing AND m.status = 'active') OR (m.status = 'superseded' AND NOT %s))",
            (project_id, list({key[0] for key in others}), include_history))).fetchall()
        def repeats(key) -> bool:
            content = normalize(found[key]["content"])
            return any(r["episode_id"] == key[0] and _quote(r["quote"]) and _quote(r["quote"]) in content for r in said)
        others = [key for key in others if not repeats(key)]
    if not broad:
        floor = max(min_relevance, (best if best is not None else min_relevance) - RELEVANCE_MARGIN)
        others = [key for key in others if relevance[key] >= floor][:OTHER_PASSAGES]

    picked, spent = [], 10
    for key in [*evidence_keys, *others]:
        cost = tokens_of(found[key]["content"]) + 4
        if spent + cost > budget:
            continue
        spent += cost
        picked.append(found[key])
    return sorted(picked, key=lambda p: (p["occurred_at"], p["episode_id"], p["ord"]))

