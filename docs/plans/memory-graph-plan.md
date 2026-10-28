# Plan: present Memvine memory as a living graph

Status: design and handoff plan, 26 Oct 2026. Nothing here is built yet.
Each workstream below is written to be handed to its own session as a brief:
it names the files, what exists already, what done looks like, and what it must
not touch. Read "Shared decisions" before any workstream; they are the words and
shapes every session must agree on.

## The idea

Memvine already stores a graph. Facts supersede other facts, conflict with each
other, hang off threads of work, are anchored to files, and carry a quote and a
speaker. None of that has a name or a picture, so the product reads as a table
of rows. This plan draws the graph, names its edges, and leads with it: in the
API, in the console, in the first five minutes, and in the docs.

The one-sentence image: **memories grow on the trellis of the code. When the
trellis moves, the vine notices.** Code-anchored staleness is the edge no other
memory product has, so it leads everywhere.

What this plan does not change: extraction, reconcile, thread pages, recall
ranking, billing, access. It adds edges and surfaces; it does not redo the
model.

## Shared decisions (every session uses these)

### Edge names

Six user-facing edges. The first four exist in the schema today; the last two
are new. Names are lower-case verbs and appear exactly like this in the API,
console and docs.

| Edge | Meaning | Today | Example |
| --- | --- | --- | --- |
| `supersedes` | A newer fact replaces an older one; the old one keeps `valid_to`. | `memory_items.supersedes` | "Launch is 12 Nov" supersedes "Launch is 29 Oct" |
| `contradicts` | Two active facts disagree and neither is newer by evidence. | `memory_items.conflicts_with` | "Retries cap at 3" contradicts "Retries cap at 5" |
| `anchored` | A fact depends on a file at a version; it goes stale when the file changes. | `memory_anchors`, `stale_reason` | "Retries cap at 3" anchored to `src/queue.py@a1b4c9e` |
| `decided_in` | A fact came out of a thread of work. | `memory_items.thread_id` | "Billing is manual for the beta" decided in release 0.4 |
| `extends` | A newer fact adds detail to an older one; both stay active. | new | "The cap exists because the broker rate-limits" extends "Retries cap at 3" |
| `derived` | A fact the model inferred from two or more memories, never stated in one place. | new | "Priya owns partner onboarding" derived from three sessions |

Two more are edges in the picture but fields on the node in the API, because
they already are: **said by** (`evidence[].identity` plus `assertion`) and **why**
(`why` on knowledge). Do not add them as edges.

### Trust words

Wherever a fact is shown to a person, `assertion` renders as plain words, never
as a badge alone: observed → "seen in a run", stated → the speaker's name
("Priya said"), claimed → "assistant said, unverified", inferred → "inferred".

### The receipt

Every fact rendered anywhere in the console uses one component: statement on
the left; trust word, date and source on the right; `why` on a second line when
present; the quote on hover; Still true, Wrong and Forget on hover. Build it
once in `console/components/` (workstream 2) and reuse it.

### The name for synthesis

Background work (extraction, reconcile, consolidate, thread rewrites) is shown
to users as **tending**: "tending 2 sessions" in a header, "tended 4 min ago".
Internal names (`worker`, `jobs`) do not change. If you prefer another word,
change it here before sessions start; every session reads it from this file.

### Memory types and their behavior

This table is the public promise. Workstream 4 documents it; workstream 5 makes
the two rows marked "to build" true.

| Type | Behavior | Status |
| --- | --- | --- |
| decision | Persists; carries a `why`; `decided_in` a thread. | true today |
| rule | Persists; may be `standing` (always on, every recall). | true today |
| gotcha | Anchored to code; goes stale when the file changes. | true today |
| fact | Persists until superseded; history kept. | true today |
| preference | Strengthens with repetition: each restatement raises confidence. | to build |
| progress | Never a fact. Updates the thread page, then expires. | refused by prompt today; expiry to build |

## Workstreams

Order: 1 and 3 and 4 can start at once. 2 starts on mock data at once and
switches to the live API when 1 lands. 5 is small and independent. 6 is last.

### 1. Edges in the API

**Area:** `server/memvine_server/` (`reconcile.py`, `recall.py`, `models.py`,
`schema.sql`, `mcp_app.py`), `sdk/typescript/src`, `sdk/python/memvine_sdk`.

**Exists:** `supersedes`, `conflicts_with`, `thread_id`, `memory_anchors` on
`memory_items`; reconcile returns `same`, `update`, `contradiction`,
`different`; recall returns `items[]` with `previous` on updated facts.

**Build:**

- A `memory_links` table: `from_id`, `to_id`, `kind` (`extends` or `derived`),
  `created_at`, `episode_id` for the evidence that created it. Idempotent DDL in
  `schema.sql` like the rest.
- Reconcile emits `extends` when the LLM judges a candidate adds detail to an
  active item sharing a subject without replacing it (today that lands as
  `different`). Add the option to the reconcile prompt and tests in
  `server/tests`.
- A `derive` step in consolidate: when a subject's page is rewritten, the model
  may return up to two derived facts, each citing two or more existing item ids;
  stored as `assertion: inferred`, `confidence: low`, with `derived` links to
  the cited items. Never from a single item. Test with the fake LLM.
- `GET /v1/memories/{id}/related`: the node plus every edge in both directions
  as `{kind, direction, item}`; `anchored` edges carry the file, the anchored
  version, and whether it is current; `decided_in` carries the thread summary.
- `POST /v1/recall` takes `include: {related: true}`; each served item gains
  `related: [{kind, direction, id, statement}]`, capped at 6 per item, excluded
  from the token budget's packing but counted in `tokens`.
- `GET /v1/graph?since=<date>&thread=<id>&subject=<name>`: nodes (facts,
  threads, files, people) and edges for a neighborhood, capped at 300 nodes,
  for the console. Nodes added or changed since `since` are flagged `grew: true`.
- MCP `memory_recall` passes `include.related` through. Both SDKs expose
  `related` on recall and a `related(id)` call. SDK READMEs open with the
  ten-line sample from workstream 4.

**Done when:** `pytest -q` and `ruff` pass; a recall with `include.related` on
the sample project returns the superseded launch date with a `supersedes` edge
and the contradiction with a `contradicts` edge; `GET /v1/graph` on the sample
project returns both plus their thread.

**Do not:** change ranking, thresholds, billing, or the shape of existing
response fields.

### 2. The graph as the console's center

**Area:** `console/app/page.tsx` (home), new `console/app/graph/`,
`console/components/`, `console/lib/types.ts`, `console/lib/api-live.ts`,
`console/lib/api-mock.ts`, `console/lib/mock-data.ts`.

**Exists:** a stats-and-cards home; Memories, Threads, Knowledge, Sources,
Entities, Playground pages; `MemoryItem` with `supersedes`, `conflictsWith`,
`staleReason`, `threadId`, `evidence`; a mock backend for UI work.

**Build:**

- `Receipt` component per "Shared decisions". Use it on the graph's node panel
  and on Knowledge, Memories and Thread pages (replace their row layouts).
- The home becomes the graph. A force layout of the project's neighborhood:
  threads as hubs, facts around them, files at the rim, people as small nodes.
  Edges styled by kind; `anchored` edges to a changed file drawn broken;
  `supersedes` drawn as an arrow from new to old with the old node dimmed;
  `contradicts` drawn as a double line. Nodes that grew this week are
  highlighted; a toggle shows only growth. Cap the first view at the open
  threads plus their facts; everything else loads on expand.
- Click a node: the view narrows to that node's neighborhood and a side panel
  shows its receipt, its history chain (superseded values with dates), its
  file and version, its thread, its sources, and its actions. Still true
  re-anchors; Wrong opens "What is true instead?" and stores the answer with
  `POST /v1/memories` so it supersedes; Forget retires.
- Header: project name and description, "tended 4 min ago · tending 2 sessions",
  one primary button "Ask memory" (the playground, renamed Ask in the sidebar).
- Empty project: workstream 3's paste screen renders instead of the graph.
- Mock data gets `extends` and `derived` links and a `grew` flag so the view
  works without a server. Type the new API fields in `lib/types.ts` to match
  workstream 1's shapes exactly (copy them from this file, do not invent).
- Library: pick one small, maintained force-graph library (d3-force with SVG
  is enough); no WebGL. Must work at phone width with pan and pinch.

**Done when:** on mock data the home shows the two demo projects' graphs with
every edge kind visible, clicking a fact shows its receipt and chain, and
Wrong on a fact creates a superseding fact in the mock store; on the live
server the same works against the sample project.

**Do not:** remove the Developer pages (Requests, Usage, Keys, Jobs); group
them under a "Developer" sidebar heading. Do not touch the server.

### 3. One transcript becomes many facts

**Area:** `console/app/` (first-run state of the home, or `console/app/try/`),
`server/memvine_server/ingest.py`, `worker.py`, `sample.py`.

**Exists:** `POST /v1/ingest` queues an episode; `GET /v1/jobs` reports it;
`POST /v1/sample` loads memory with no model call; the plugin's backfill.

**Build:**

- An `instant: true` option on `POST /v1/ingest` for a single small episode
  (under 4,000 characters): the request runs extraction and reconcile inline
  and returns the created items with their edges, instead of queueing. Same
  billing as a queued episode. Reject with 400 for batches or larger text.
- The empty-project screen: a text box with a prompt ("Paste a few lines from
  a session, a standup, or a PR thread"), a sample snippet prefilled, and a
  button. On submit the facts appear one by one with their edges drawn, using
  workstream 2's node and receipt components (if 2 has not landed, a plain
  list with the receipt layout, and the graph swaps in later). Under it, the
  three connect steps (key, install, backfill) and the sample-data offer.
- The same box lives on the Ask page as "Add" so a person can feed memory by
  hand at any time.

**Done when:** a new project with no sources shows the paste box; pasting the
prefilled snippet produces at least three facts with one `extends` or
`supersedes` edge within the request; the connect steps follow.

**Do not:** change what the plugin sends or how sessions are sealed.

### 4. Docs and README: lead with the picture and ten lines of code

**Area:** `README.md`, `server/README.md`, `sdk/*/README.md`, new
`docs/how-memory-works.md`, `docs/architecture.svg` (replace or add a graph
image).

**Build:**

- `docs/how-memory-works.md`, in this order: the one-sentence image; a
  ten-line connect/send/recall sample (TypeScript, Python beneath it); sources
  vs memories as a two-column table (what, examples, who changes it,
  lifecycle); the six edges, each with a two-line example from a coding
  project (use the examples in "Shared decisions"); one transcript becomes
  many facts, with a real six-line session excerpt and the facts it yields;
  tending (what runs in the background and when the result is visible);
  memory types and behavior (the table above, verbatim); forgetting, in this
  order: code changed, superseded, retention, refused as noise; what you don't
  do (no schema, no tags, no pruning, no prompt engineering: connect once).
- A graph image for the top of that page and the README: the sample project's
  graph, exported from workstream 2 once it renders, or hand-drawn SVG in the
  same style until then. Stale anchored edges visibly broken.
- The root README leads with the image and the ten lines; everything about
  the git-native CLI moves below a heading "The local, git-native version".
- Every README uses the edge names, trust words and "tending" from this file.

**Done when:** a reader who knows nothing about Memvine can say what a memory
is, name the six edges, and run the sample, from the docs page alone.

**Do not:** describe features that are not built; where a workstream is
pending, write for its "Done when" state and mark the section "landing in
<workstream>" in an HTML comment.

### 5. Make the type table true

**Area:** `server/memvine_server/reconcile.py`, `extraction/`, `consolidate.py`,
`threads.py`.

**Build:**

- Preferences strengthen: when reconcile judges a candidate `same` as an
  active `preference`, raise its confidence one step (low → medium → high) and
  record the restatement as evidence. Already-high stays high.
- Progress expires: items that extraction or the pattern check classify as
  progress never become `memory_items` (true today); confirm a thread page's
  State and Next steps items older than 30 days with no newer source are
  dropped at the next rewrite, and that this is covered by a test.

**Done when:** tests cover both; the sample project, restated twice, shows a
preference at high confidence with three evidence quotes.

### 6. The agent sees the same graph

**Area:** `plugins/claude-code/` (hook that builds the session brief),
`plugins/codex/`, `server/memvine_server/recall.py` briefing text.

**Build, after 1:**

- The briefing text names edges inline in the same words: "[decision 21 Oct,
  supersedes 29 Oct] Launch is 12 Nov", "[gotcha 3 Oct, anchored src/queue.py,
  MAY BE OUTDATED] …", "[inferred, derived from 3 sessions] …".
- The session-end line the plugin prints: "Memory kept 2 decisions and 1
  gotcha; 1 fact superseded." so a person sees memory being made and can say
  "forget the last one".

**Done when:** a session on the sample project shows the superseded launch
date with its edge in the brief, and the end-of-session line appears.

## Out of scope for all sessions

Sign-in and members, billing collection, audiences UI, identifier-level
anchors, the local git-native CLI. A session that finds it needs one of these
stops and says so instead of building it.
