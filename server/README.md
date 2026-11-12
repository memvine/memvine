# Memvine server

The memory platform's API and its tending worker: conversations go in as
sources (episodes), tending turns them into thread pages and dated knowledge
items with evidence and reconciles them against what is already known, and
recall returns a budgeted briefing an agent can use as is.

What a memory is, the edges between memories (`supersedes`, `contradicts`,
`anchored`, `decided_in`, `extends`, `derived`) and how memory is forgotten:
[How Memvine memory works](../docs/how-memory-works.md). This page is the
operator's and API reference.

Design: the platform design doc, sections "Core concepts and data model",
"Synthesis and compaction", "Recall" and "Public API".

## Run it

```sh
cd server
docker compose up -d                  # Postgres 16 + pgvector on localhost:5433
uv venv --python 3.12 .venv && uv pip install -e ".[dev]"
.venv/bin/memvine-server create-project "My project"   # prints an mv_sk_ key once
.venv/bin/memvine-server serve                          # http://127.0.0.1:8400, worker included
```

Configuration is `MEMVINE_*` environment variables or `server/.env`; see
`memvine_server/config.py`. The two that matter first:

| Variable | Values | Default |
| --- | --- | --- |
| `MEMVINE_LLM_BACKEND` | `anthropic` (needs `ANTHROPIC_API_KEY`), `claude-cli` (local `claude -p` on a Claude subscription; development only), `fake` | `anthropic` |
| `MEMVINE_EMBEDDINGS_BACKEND` | `fastembed` (local BGE-small, downloaded once), `fake` | `fastembed` |
| `MEMVINE_RECALL_RERANK_MODEL` | Conversation mode: the local model that orders candidate details against the question (about 150 MB, downloaded once); empty turns it off | `jinaai/jina-reranker-v1-turbo-en` |
| `MEMVINE_RECALL_DETAIL_SUMMARY_DIGEST_SHARE` | Conversation mode: the share of a summary question's budget given to digests of the conversation (`memvine-server backfill-digests <slug>` writes them for an older store) | `0.85` |

On the public internet also read `docs/security.md`: rate limits (`MEMVINE_RATE_LIMIT_*`), the body
cap (`MEMVINE_MAX_BODY_BYTES`, `MEMVINE_MAX_BULK_BODY_BYTES`), and which proxies' `X-Forwarded-For`
is believed (`MEMVINE_TRUSTED_PROXIES`, default `private`).

## Use it

```sh
KEY=mv_sk_...
curl -s localhost:8400/v1/ingest -H "Authorization: Bearer $KEY" -H 'content-type: application/json' -d '{
  "items": [{"id": "chat-1", "timestamp": "2026-10-03T14:02:00Z", "identity": "priya@example.com",
             "content": "## Developer\nLegal pushed back on the refund copy, so launch moves from 29 Oct to 12 Nov."}]}'

curl -s localhost:8400/v1/jobs -H "Authorization: Bearer $KEY"        # wait until pending is 0

curl -s localhost:8400/v1/recall -H "Authorization: Bearer $KEY" -H 'content-type: application/json' \
  -d '{"query": "When is the launch?", "budget": 2000}'
```

| Route | Purpose |
| --- | --- |
| `POST /v1/ingest` | Batch of episodes (`id`, `content`, `timestamp`, optional `identity`, `label`, `metadata`, `audience`, `code`). Same id + same content: no-op and free. Same id + new content: replaces it and everything derived from it. With `instant: true` (one episode, at most 4,000 characters, else 400): learned from within the request and returned as `instant.memories`, each with its edges; billed the same. |
| `POST /v1/recall` | `query`, optional `budget` (tokens), `identity`, `as_of`, `exclude`, `include_history`, `session_id`, `branch`. Returns `briefing` plus `standing`, `pages`, `threads`, `items`, `passages`, `pending`, `tokens`, `abstained`, `abstain_reason`, `min_relevance`, `top_relevance` (see Recall below). |
| `POST /v1/memories` | Store one fact directly (`statement`, `kind`, `subjects`, `assertion`, `audience`, `standing`, `category`, `why`); reconciled like extracted facts. With `supersedes: <memory id>` the caller says which fact this replaces (a person's correction): no judge is asked, it supersedes that memory (`relation: "update"`, `related: <old id>`) and inherits its kind, subjects, category, thread, scope and audience unless given. The old memory must be the project's, active or stale (else 404 `not-found` / 400 `memory-not-current`), and in the same audience (400 `audience-mismatch`). |
| `GET /v1/memories`, `GET /v1/memories/{id}` | Browse items with evidence; filters `status`, `kind`, `category`, `scope`, `q`, `unused`, `review`. `status=expired`: items past the end they stated (`valid_to`; `expired: true`), which `status=active` leaves out. `unused=true`: items older than `MEMVINE_MEMORY_UNUSED_DAYS` (60) that no recall served in that time (`last_recalled_at`, `recall_count`), for review; nothing is hidden or deleted for it. Each item has `review` (`new` until a person keeps it; a person's own memories start `kept`) and `origin` (`synthesis`, `person`, `sample`); `review=new` lists current memories awaiting review, and projects carry `review_count`. |
| `GET /v1/memories/{id}/related` | The memory and every edge, both directions, as `{kind, direction}` plus `item`, `file` or `thread`. |
| `GET /v1/graph?since=&thread=&subject=&all=` | A neighbourhood of the memory graph for the console: `nodes` (facts, threads, files, people), `edges`, `truncated`; at most 300 nodes. Without `thread`/`subject` it is the open threads and the most recent facts; `all=true` adds every thread whatever its status (`truncated` when the cap left something out). |
| `GET /v1/threads`, `GET /v1/threads/{id}` | Thread pages, one per piece of work; filters `status`, `kind`, `q`. One thread adds its `sections`, `related`, `knowledge` and `sources`. |
| `PATCH /v1/memories/{id}` | `{"status": "retired" or "active"}`, `{"confirm": true}` (Keep / Still true: also sets `review: kept`). Retiring a memory that superseded an older fact makes that fact current again. Retiring is a person forgetting: synthesis never learns a matching memory again (same wording or cosine >= 0.92; counted as `dropped_forgotten` in the job result), and a synthesis-made memory is recorded as a feedback case. Restoring it (`active`) undoes both. |
| `GET /v1/feedback-cases` | Owners and admins: synthesis-made memories people forgot (`kind: forgotten`) or corrected (`kind: wrong`, with `correction`), each with `statement`, `category`, `quotes` and a source `excerpt` (at most ~1500 characters around the first quote). Deleted with the project or the excerpt's source; included in `POST /v1/export`. `memvine-server feedback-export <slug> [--out FILE]` writes them as JSONL for the local gate (`benchmark/knowledge/cases.py`, `~/.memvine/eval/drop-cases.jsonl`). |
| `DELETE /v1/episodes/{id}` | Delete a source and the memories drawn only from it; a superseded value whose successor is deleted becomes current again. |
| `GET /v1/episodes`, `GET /v1/episodes/{id}` | Sources with their synthesis status and memory count; one source's content. |
| `POST /v1/sessions/{id}/events`, `GET /v1/sessions[/{id}]`, `GET .../brief`, `POST .../close`, `GET .../kept` | Live sessions: stream messages (optional `code`; `live: false` imports a past session), read the working state, get a brief after compaction, end the session, and see what memory kept from it (`kept`: the memories first drawn from it, `superseded`, `contradictions`, `tending` while synthesis runs, and a `summary` line such as "Memory kept 2 decisions and 1 gotcha; 1 fact superseded."). |
| `GET /v1/code/anchors?repo=`, `POST /v1/code/check` | Code-change staleness (below): which files memories are anchored to; report their current versions. |
| `POST /v1/sample`, `DELETE /v1/sample` | Load a small sample memory (sources marked `metadata.source = "sample"`, a superseded launch date and a contradiction) to try recall on, written directly with no model call and no credits; loading again is a no-op. Delete removes the sample sources and their memories. `POST /v1/projects` takes `"sample": true` to start with it. |
| `GET /v1/jobs`, `POST /v1/jobs/{id}/retry` | Synthesis queue with per-episode results and errors; retry a failed job. |
| `GET /v1/requests`, `GET /v1/usage` | Request log (a recall's `detail` says what it served or why it abstained); billable calls and credits per day. |
| `PUT/GET/DELETE /v1/audiences/{slug}`, `PUT/GET/DELETE /v1/identities/{id}` | Who may read which memory (see Access). |
| `GET/POST /v1/projects`, `PATCH/DELETE /v1/projects/{slug}`, `GET/POST /v1/projects/{slug}/keys`, `DELETE .../keys/{id}` | Projects (name, tags, `monthly_credit_cap`, `retention_days`, `description`, `recall_min_relevance`, `recall_half_life_days`, `memory_mode`: `coding` or `conversation`, see `docs/how-memory-works.md`) and project keys, under a management key. Delete needs `?confirm=<slug>`. |
| `GET /v1/org`, `GET/POST /v1/org/keys`, `DELETE /v1/org/keys/{id}` | Who is calling (person, role, organizations), and the organization's keys (never the last one). |
| `POST /v1/auth/signup`, `/login`, `/logout`, `/switch`, `/accept`, `/password`; `GET /v1/auth/config`, `POST /v1/auth/invites/describe` | Accounts: email and password, sign-in sessions (`mv_us_`), invite links, organization switching. Sign-up follows `MEMVINE_SIGNUP` (`open`, `invite`, `closed`). |
| `GET /v1/org/members`, `PATCH/DELETE /v1/org/members/{user}`, `GET/POST /v1/org/invites`, `DELETE /v1/org/invites/{id}` | People and roles (owner, admin, member), invite links. |
| `GET /v1/audit` | Administrative changes and memory edits, per organization. |
| `GET/POST /v1/orgs` | Operator only: list and create organizations (the first key is returned once). |
| `/mcp` | MCP over streamable HTTP with a project key: `memory_recall`, `memory_remember`, `memory_session_brief`, `memory_forget`. |

Keys: a project key (`mv_sk_`) acts on one project. People sign in with email and password and
get a sign-in session (`mv_us_`) carrying their role: owners and admins manage projects, keys,
people, audiences and sources; members use memory and create project keys for their agents. An
organization key (`mv_mk_`) acts with full rights, for scripts and CI. Sessions and organization
keys can call project routes for one project with `X-Memvine-Project: <slug>` (the console does).
The operator key (`MEMVINE_MANAGEMENT_KEY`) administers every organization.

Isolation and secrets: the server runs as the Postgres role `memvine_app`, and row-level security
confines a project-scoped request's connection to that project's rows beneath the queries' own
filters. Every text the API accepts is redacted (keys, tokens, bearer headers, JWTs, private keys,
secret-named assignments) before it is stored, embedded or shown to a model.

Code-change staleness: a source or session may carry `code` (`repo`, `files`: repo-relative path ->
git blob hash) for the files the conversation touched. A fact about current code (semantic or
procedural) that names one of those files is anchored to that version. `POST /v1/code/check` with
the versions a client has now marks memories whose files changed (or were deleted) `stale`, with a
`stale_reason`, and marks them active again when the files match again; recall shows them with
"MAY BE OUTDATED". Restating the fact in a later conversation, or confirming it, re-anchors it.
The Claude Code plugin does all of this itself.

Billing: 1 credit per 1,000 characters ingested (a re-send of identical text is free), 0.1 per
recall made with a project key. Sample data costs nothing. A project at its monthly cap gets `429 project-cap-reached` on
ingest and session events; recall is never refused. Retention drops old raw text and keeps
memories.

Recall and abstention: recall serves nothing unless something is relevant enough. A memory's
relevance is its cosine similarity to the question, plus 0.05 when it also matches the question's
words (the fused ranking score in `items[].score` orders results but says nothing about absolute
relevance). The threshold is the project's `recall_min_relevance` (`PATCH /v1/projects/{slug}`;
null resets it), else `MEMVINE_RECALL_MIN_RELEVANCE`, else the embedding model's calibrated default:
0.55 for BGE-small, where off-topic questions score about 0.40-0.47 against a project's memories and
on-topic ones 0.64-0.80. Besides a memory, only these count as a hit: the thread
page of the caller's `branch` (branch-first: an agent on a branch always gets its work's page) or a
page the question names by key or alias; and a pending message sharing a content word with the
question. A thread page found only by similarity is background and never a hit; it is served only
when it is as similar as a hit must be.
On abstention `abstained` is true, `items`, `threads`, `passages` and `pending` are empty,
`briefing` is `""` and `tokens` is 0; `abstain_reason` is `empty_store` (no memory to search),
`no_candidates` (memory exists but none is visible to this question: all excluded, or nothing
recorded by `as_of`) or `below_threshold`; `min_relevance` and `top_relevance` say how close it came.

A served briefing carries what the question needs and says nothing twice. For a question that is not
broad (summaries, overviews, "how many", ... keep their breadth), items are kept only within 0.10
(`RELEVANCE_MARGIN`) of the best item's relevance; the top item always stays, and an item that
contradicts, supersedes or is superseded by a kept one stays with it. A thread page is served when it
is the caller's branch page, named by the question, or within the same margin of the best relevance
(and above the threshold). Source excerpts are the evidence of the kept items (the passage holding
each quote), plus at most one other passage within the margin; never the source of a standing rule,
nor of a superseded value unless the question asks for history (`include_history`). A standing rule
is not repeated as an item or inside a page; a page leaves out the section items and thread
knowledge the briefing already says (same words, or the same evidence quote) and every line that
states a superseded value; a page found by similarity also loses lines citing the source episode of
a kept or superseded item, is dropped when nothing is left, and is dropped outright when its title
and summary are about a replaced value (they name the old value and not the new one). A superseded value whose replacement is served is said once,
as `(was: ...)`; it stays in `items`.

Ranking and recency: items are ordered by their fused rank score (reciprocal rank over the vector and
keyword lists) times the trust and currency weights, times a recency factor and a confirmation boost.
Among comparably relevant memories, a fact or gotcha said recently ranks above one last said long
ago: its score is multiplied by `0.6 + 0.4 * 0.5 ** (age_days / half_life)` (`RECENCY_FLOOR` 0.6, so
an old fact that is the best match is still served), where age runs from when it was last said (the
later of `recorded_at` and its newest evidence episode, so a restatement makes it recent again).
Decisions, rules, preferences and standing rules never decay: they are true until superseded. Memories
without a category decay only as plain facts or events (`semantic`, `episodic`). The half-life is the
project's `recall_half_life_days` (`PATCH /v1/projects/{slug}`, or the console's project settings;
null resets it, 0 turns recency off), else `MEMVINE_RECALL_HALF_LIFE_DAYS` (default 90). A history
question (`include_history`, `as_of`, or history words such as "previously") gets no recency. A
memory a person confirmed (`review = 'kept'`: Keep, or written by a person) gets ×1.1
(`KEPT_BOOST`). Both change only the order: the relevance gate and `top_relevance` use raw relevance,
the trim margin is on relevance, and the item the trim always keeps is the first by the score before
recency, so recency reorders what the margin keeps but never brings in what it would drop.

Standing instructions (`standing: true` memories) are always-on context, not search results: every
recall returns them in `standing`, including an abstained one, so a caller may choose to apply them.
In a served briefing they come first, in their own section headed `=== memvine · standing
instructions ... ===`, ahead of the recalled memory. They never count as a hit, so a project's
rules cannot make an unrelated question look answered (a standing rule that itself matches the
question counts like any memory). The MCP `memory_recall` tool returns them after its "nothing
matches" line when it abstains.

Each fact line in a briefing leads with what the memory is, when it was said, its trust when lower
than a person or a run ("assistant said, unverified", "inferred") and its edges, then the statement
and any neighbour it needs: `[decision 15 Oct, supersedes 6 Oct] The launch moved to 14 Nov (was:
The launch is 31 Oct)`, `[rule 26 Oct, contradicts 24 Oct] ...`, `[fact 22 Oct, extends 21 Oct] ...
(adds to: ...)`, `[fact 23 Oct, inferred, derived from 3 memories] ...`, `[gotcha 3 Oct, anchored
src/queue.py, MAY BE OUTDATED] ... (src/queue.py changed since 20 Oct 2026; check the code)`. A
contradiction already paired at the top of the briefing is named on its lines, not quoted again.
These words cost tokens like the rest of the line; they never change what is ranked or packed.

Access: an episode may name an `audience` (default `project`, everyone). Memory drawn from it
inherits that audience, merges or supersedes only within it, and stays off thread pages unless
it is project-visible. A recall naming an `identity` reads `project` plus that identity's
audiences; a recall with no identity reads `project` only, except with the management key (the
console), which reads everything.

## How tending works

Tending is the background work (the `worker` and its `jobs`) that turns sources into memory: thread
pages for the work in progress, plus a small set of durable knowledge (`synthesis.py`,
`threads.py`). A project description (`"description": "what the project is"` on create or
`PATCH /v1/projects/{slug}`) is what extraction judges relevance against. Per episode:

1. **Threads.** One living page per piece of work (release, task, PR, ticket, topic), edited in
   place. Candidate keys come from the text before any model call: the branch in a session label
   (`Claude Code · <folder> · <branch>`; main, master, HEAD and develop are skipped), `PR #440`,
   ticket ids like `INV-255`, release versions (`release 4.2.0`, `v4.2.0`). They load the
   existing pages (by key or alias), and the model returns each changed page whole: title,
   status, a 2-3 sentence summary, and sections from a fixed vocabulary (Goal, State, Contains,
   Done, Decisions, Left out, Deferred, Open questions, Next steps, Outcome; at most 8 items each).
   Every item needs a quote found verbatim in the episode or kept unchanged from the old page.
   State and Next steps items expire: an item whose newest source is more than 30 days older than
   the episode being written is dropped at that rewrite.
2. **Knowledge.** Only durable, self-contained items pass: `decision`, `rule`, `gotcha`, `fact`,
   `preference`, each with a `why` and its thread. Progress events, status snapshots, unaccepted
   plans, debugging guesses and tooling trivia are refused by the prompt, and progress events
   and status snapshots also by a pattern check. Each item carries a verbatim quote, checked
   against the source text; an assistant's report on its own work ("all tests pass") is forced to
   `claimed`. Knowledge that only matters inside one piece of work is scoped to its thread and
   shown only beside that page.
3. **Reconcile.** Each knowledge item is compared with active items sharing a subject
   and close in embedding space. Near-identical wording merges without a model
   call, and so does a near-duplicate (cosine ≥ 0.92, same kind and category, same numbers, no
   change words such as "now" or "moved"): it keeps the existing wording and adds evidence; otherwise the LLM says `same` (merge evidence; a restated preference strengthens one
   step: low, medium, high), `update` (the old item is superseded, `valid_to` set, both linked;
   also when the new item settles something the old one called pending), `contradiction` (both
   kept, side by side), `extends` (both kept, linked) or `different`.
   An item that states its own end ("the scratch org expires on 28 Oct") is stored with that end
   as `valid_to`; past it, it keeps its status but is expired: recall no longer serves it as
   current (a historical question or an earlier `as_of` still sees it, marked "ended").
4. **Recall.** Standing rules, then the one or two thread pages the question is about (within 45%
   of the budget, withheld for an `as_of` earlier than anything the page knows), then knowledge
   (`[decision 21 Oct] ...`): vector and keyword search fused by reciprocal rank, then weighted;
   superseded items only when the question asks about history or passes `as_of`; agent claims at
   half weight; the previous value inline on updated facts; trimmed to the relevance margin. Then the
   kept items' source excerpts. Empty when nothing clears the threshold.

Thread pages are written from project-visible episodes only. Deleting a source takes its
evidence off the pages; a thread left with no source is deleted.

## Edges

Four edges are fields on every memory today: `supersedes` (`supersedes`, `superseded_by`,
`valid_to`), `contradicts` (`conflicts_with`), `anchored` (the anchors behind `/v1/code/anchors`,
and `stale_reason` when the file changed) and `decided_in` (`thread_id`). Who said it and why
are fields too: `assertion` with the `identity` of the source each `evidence` quote comes from (shown to people as "seen in a run",
"Priya said", "assistant said, unverified", "inferred") and `why` on knowledge.

`extends` and `derived` are stored in `memory_links`. `GET /v1/memories/{id}/related` returns a
memory and every edge in both directions as `{kind, direction, item}`; `anchored` edges carry the
file, the anchored version and whether it is current, `decided_in` the thread summary.
`POST /v1/recall` with `include: {related: true}` adds `related: [{kind, direction, id,
statement}]` to each item (at most 6). `GET /v1/graph?since=&thread=&subject=&all=` returns a
neighbourhood of facts, threads, files and people (at most 300 nodes), with `grew: true` on nodes
changed since `since`; `all=true` includes every thread, not only the open ones.

A person's correction is an explicit `supersedes` edge: `POST /v1/memories` with `supersedes: <id>`
(MCP `memory_remember(..., supersedes=id)`) replaces that memory without asking the judge, so the
edge is recorded even when the new statement reads as a different fact, and the correction stays in
the old fact's thread and category.

## Live sessions and rolling checkpoints

`POST /v1/sessions/{id}/events` stores each message durably the moment it arrives. Completed
stretches are sealed into episodes while the session runs (`checkpoints.py`):

- once the unsealed text passes `MEMVINE_SESSION_SOFT_CHARS` (6,000), cut at the next completed
  exchange, never between a question and its answer;
- once the oldest unsealed message is `MEMVINE_SESSION_MAX_AGE_MINUTES` (10) old, measured from
  that message, so a slow steady conversation is still processed;
- everything left when the session ends (`POST .../close`, or 30 idle minutes).

`MEMVINE_SESSION_MAX_TOKENS` (8,000) caps one extraction's input. A sealed range has a stable id
(`session:<id>:<from>-<to>`) and is never re-cut. Its extraction reads the end of the previous
range and the most similar existing memories as context (never quotable as evidence), and
`processed_through` advances in the same transaction as the memory writes. A retried extraction
replaces what that episode produced. Until a range is extracted, recall shows its messages to
other sessions of the project as pending (`session_id` on recall leaves out the caller's own).

## MCP

The same memory over MCP (streamable HTTP, stateless) at `/mcp`, authenticated with a project
key in the Authorization header. Tools: `memory_recall`, `memory_remember` (optional `supersedes`:
the id of the memory a correction replaces), `memory_forget`, `memory_session_brief`. For Claude Code:

```sh
claude mcp add --transport http memvine http://127.0.0.1:8400/mcp --header "Authorization: Bearer mv_sk_..."
```

## Schema and migrations

The schema is a series of numbered SQL files in `memvine_server/migrations/` (`0001_baseline.sql`,
then `000N_<name>.sql`), plus `rls.sql`, the row-level security setup. On start the server applies
every pending file in order, each in its own transaction, under a Postgres advisory lock (an API and
a separate worker starting together apply each file once), and records it in `schema_migrations`
with a checksum. `rls.sql` is repeatable: it runs again after any numbered migration (so new tables
get its grants) and whenever its text changes; it is recorded in `schema_repeatables`.

```sh
.venv/bin/memvine-server migrations         # applied (with dates) and pending
.venv/bin/memvine-server migrate --dry-run  # what the next start would do
.venv/bin/memvine-server migrate            # apply now, without starting the server
```

**Upgrading a server.** Take a backup, then deploy the new code and restart; the server migrates
before it accepts requests. A migration that fails rolls back and the server does not start: fix
the cause (or return to the old code) and start again. `MEMVINE_AUTO_MIGRATE=false` makes the
server refuse to start while migrations are pending, so they run only through `memvine-server
migrate`.

**Writing a migration.** Add the next number, e.g. `0003_project_quotas.sql`. Never edit a file
that has been applied anywhere: the server compares checksums on every start and refuses to run if
an applied file changed (fix forward with a new file). A table holding project rows also goes into
the list in `rls.sql`. A statement that cannot run in a transaction (`CREATE INDEX CONCURRENTLY`)
goes alone in its own file whose first line is `-- memvine:no-transaction`.

**Databases from before migrations.** Until migrations existed the server applied one idempotent
`schema.sql` on start (tracked in `schema_meta`). A database with Memvine's tables but no recorded
migration is baselined on its first start: the server checks that every table and column of
`0001_baseline.sql` exists, records 0001 as applied without running it, and reapplies `rls.sql`.
If anything is missing it refuses to start and names it. `schema_meta` is left in place, unused.
`0002_memory_aging` and `0003_review_loop` were the last blocks added to `schema.sql`; they keep
their `IF NOT EXISTS` forms, so a database that already ran them runs them again harmlessly.

## Synthesis health and alerts

`GET /v1/health/synthesis` (owners and admins for their organization's projects, the operator for
all; `?project=<slug>` narrows to one) reports what leaves memory silently behind:

- **failed**: sources whose synthesis used every attempt in the last `MEMVINE_ALERT_FAILED_HOURS` (24)
- **stuck**: a live (not imported) source waiting longer than `MEMVINE_ALERT_QUEUE_MINUTES` (30) in a
  project that is not paused
- **worker**: no worker has polled the queue for `MEMVINE_ALERT_WORKER_MINUTES` (10); each worker
  process records a heartbeat in `worker_heartbeats` as it polls

The console shows the project's issues as a banner on Home and Sources. Set
`MEMVINE_ALERT_WEBHOOK_URL` to a Slack incoming webhook (or anything taking `{"text": ...}`) and the
worker posts each condition at most once per `MEMVINE_ALERT_REPEAT_MINUTES` (60), checking every
minute, whichever worker process notices first. A worker whose process is gone cannot post that it
is gone; the banner and the endpoint still show it.

## Docker

`docker build -t memvine-server .` then run it with `MEMVINE_DATABASE_URL`, `MEMVINE_MANAGEMENT_KEY`
and `ANTHROPIC_API_KEY` set; mount a volume at `/var/cache/fastembed` to keep the embedding model.

The image installs the exact versions in `requirements.lock`, so two builds of one commit hold the
same packages. `pyproject.toml` keeps the ranges; after changing it, or to take updates, rebuild
the lock, audit it and run the tests on it:

```sh
uv pip compile pyproject.toml --python-version 3.12 --universal --no-header -o requirements.lock
uvx pip-audit -r requirements.lock --no-deps --disable-pip
```

## Tests

```sh
docker compose up -d
.venv/bin/pytest -q          # DB tests skip when Postgres is not reachable
.venv/bin/ruff check memvine_server tests
```

Tests use the fake LLM and fake embedder, so they are deterministic and free.

## Not yet built

Payment collection, SSO, identifier-level staleness (anchors are per file), per-request rate
limits beyond sign-in throttling. See the
design doc's roadmap.
