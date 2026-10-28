# Plan: a context graph built from memory

Status: design and handoff plan, 27 Oct 2026. Supersedes the console half of
`memory-graph-plan.md`: the fact graph on the home is replaced. Each workstream
is a brief for its own session. Read "Shared decisions" first; every session
uses those words and shapes exactly.

## Why

The current home draws facts as nodes. People and agents do not think in facts;
they think in things: the service, the table, the PR, the person who owns it.
Those things exist today only as words inside facts (`subjects` on
`memory_items`). On a real project, 726 memories carry 422
distinct subjects, untyped and unresolved ("pr 440", "pr #440", "PR 440";
"invoicebot", "invoice bot"), and only 3 memories are anchored to code.

Uber's AI Context Graph (24M nodes, 80M edges, 86 node types, 117 edge types,
30 source systems, queried by agents in natural language) shows what this is
for: agents spend most turns locating information. Grounded in the graph, the
same model answered in 38 s and was right; ungrounded it took 20 min, two
subagents, three errors, and was wrong.

Memvine's version: **entities are the nodes, typed relations are the edges,
and memories are the evidence on them.** Every edge says who said it, when, and
whether it is still true, because it is backed by memory items that supersede,
contradict and go stale. That is what a schema-only graph cannot say.

Phase 1 builds the graph from memory and code. Phase 2 adds a GitHub connector.
Agents query it through an MCP tool, `context_query`.

## Shared decisions

### Entity types (node types)

Fixed vocabulary, grouped into domains (domains color the console graph).
Anything that fits none is `concept`. Names are lower-case and appear exactly
like this in the API, console and docs.

| Domain | Types |
| --- | --- |
| Code | `repo`, `service`, `module`, `file`, `endpoint`, `config` (env var, flag, setting) |
| Data | `table` (table, dataset, collection), `queue` (topic, queue, stream) |
| Work | `ticket`, `pr`, `release`, `branch`, `incident` |
| People | `person`, `team` |
| Platform | `tool` (CI, Sonar, PagerDuty, a CLI), `environment` (prod, staging, a QA org), `external` (vendor, third-party API) |
| Knowledge | `doc`, `concept` |

### Canonical keys (entity resolution)

An entity's identity is `type:key`. Typed ids are deterministic and never go
through a model:

- `pr:440` from "PR 440", "pr #440", "#440" in a PR context, GitHub URLs
- `ticket:INV-255` (upper-case project key), `release:4.2.0`, `branch:<name>`
- `file:<repo>/<path>` from code anchors and paths; `repo:<owner>/<name>`
- `person:<identity>` from episode identities; display names map to identities
  through `identities` and aliases
- everything else: `type:<normalized name>` (lower-case, punctuation and
  spaces collapsed: "invoice bot", "invoicebot", "Invoice-Bot" →
  `service:invoicebot`), plus an `aliases` list

Merging beyond that (same thing, different names) uses alias match, then
embedding similarity >= 0.92 with the same type (0.88 merged "deploy" into "production deploy" on real data), then a fast-model yes/no only
for the borderline band (0.85–0.92). Merges are recorded and reversible
(`entity_merges`), and a person can merge or split in the console.

Threads keep their keys; a thread's key is also an entity key (`pr:440`,
`ticket:INV-255`, `release:4.2.0`, `branch:x`), so a thread page is the summary
of its entity.

### Edge types (relations)

Directed, lower-case verbs, from → to. ~20 to start; add only with a reason.

| Edge | From → to | Example |
| --- | --- | --- |
| `owns` | person/team → service/module/file/table/repo | Priya owns billing-export |
| `works_on` | person → ticket/pr/release/branch | Sam works on INV-255 |
| `authored` | person → pr/doc | Sam authored PR 440 |
| `reviewed` | person → pr | Dana reviewed PR 440 |
| `fixes` | pr → ticket/incident | PR 440 fixes INV-255 |
| `part_of` | pr → release; file → module; module → service/repo; service → repo; person → team | PR 440 part of release 4.2.0 |
| `depends_on` | service/module → service/table/queue/external/tool | export depends on billing API |
| `calls` | service → endpoint/external | export calls /v2/invoices |
| `reads` / `writes` | service/module → table/queue | export reads invoices |
| `defined_in` | endpoint/config/module → file | RETRY_CAP defined in src/queue.py |
| `configured_by` | service/repo → config | export configured by RETRY_CAP |
| `deployed_to` | service/release → environment | 4.2.0 deployed to qa-org |
| `caused` | pr/release/config → incident | PR 431 caused INC-12 |
| `blocks` | ticket/pr → ticket/pr/release | INV-182 blocks release 4.2.1 |
| `replaces` | any → same type | the new exporter replaces the CSV exporter |
| `documented_in` | any → doc | export documented in the runbook |
| `uses` | person/service/repo → tool | CI uses Sonar |
| `related_to` | any → any | fallback, lowest weight, never shown by default |

### What an edge carries

Every edge is backed by evidence, never asserted bare:

- `evidence`: the memory item ids (and through them episode ids and quotes)
  that state the relation, plus deterministic sources (`code`, `manifest`,
  `github`) with no memory item
- `status`: `current`, `ended` (every memory behind it is superseded or
  retired: kept as history with `valid_to`), `stale` (its memories are stale
  because code changed)
- `trust`: the strongest assertion behind it, in the trust words of
  `memory-graph-plan.md` (seen in a run, <name> said, assistant said
  unverified, inferred); `source: code|manifest|github` reads "from the repo"
- `first_seen`, `last_seen`, `count` (how many sources)

Memories are not nodes. A memory is linked to the entities it is about
(`memory_entities`, role `subject` or `object`), and its own fact-to-fact edges
(supersedes, contradicts, extends, derived) are shown inside the entity panel as
history, not drawn on the graph.

### API shapes

```
GET  /v1/context/stats
  {"entities": 1240, "edges": 3810, "entity_types": 17, "edge_types": 14,
   "by_type": {"service": 40, ...}, "by_edge": {"owns": 31, ...}}

GET  /v1/context/graph?type=&q=&around=<entity id>&hops=1|2&all=true
  {"nodes": [{"id", "type", "domain", "name", "key", "aliases": [],
              "summary", "degree", "grew": bool, "status"}],
   "edges": [{"id", "type", "from", "to", "status", "trust", "count",
              "first_seen", "last_seen"}],
   "truncated": bool}
  Capped at 500 nodes. Without filters: the highest-degree entities and edges
  among them.

GET  /v1/context/entities?q=&type=          search (name, alias, embedding)
GET  /v1/context/entities/{id}
  {"entity": {...node, "first_seen", "last_seen", "thread": RelatedThread|null},
   "edges": [{...edge, "other": node, "direction": "out"|"in",
              "evidence": [{"memory": MemoryOut|null, "source": "memory"|"code"|"manifest"|"github",
                            "quote", "episode_id", "identity", "at"}]}],
   "memories": [MemoryOut ...]}     facts about it, newest first, with history
POST /v1/context/entities/merge {"into": id, "from": [ids]}
POST /v1/context/entities/{id}/split {"aliases": [...]}

POST /v1/context/query {"question": str, "budget": int?, "hops": 1|2?}
  {"answer_context": str,         compact text for an agent (below)
   "entities": [node], "edges": [edge], "memories": [MemoryOut ids],
   "abstained": bool, "tokens": int}
```

`answer_context` reads like this, and is what the MCP tool returns:

```
=== memvine · context (what is known, with sources) ===
service billing-export  (owned by Priya; part of repo acme/app)
  reads table invoices            — Sam said, 21 Oct (2 sources)
  depends_on external billing API — seen in a run, 27 Oct
  configured_by config RETRY_CAP  — from the repo: src/export/config.py
  [decision 27 Oct] Export retries cap at 3 per batch (was: 5)
table invoices
  read by billing-export, finance-report
Not known: who owns table invoices.
```

The last line matters: say what the graph does not know, so the agent looks
instead of guessing.

## Workstreams

Order: A, B and D start at once (D on mock data from the shapes above). C
starts when A's schema lands. E follows D. F is phase 2.

### A. Entities and relations from memory (server)

**Area:** `server/memvine_server/` — new `context.py` (resolution, storage),
`schema.sql`, `extraction/prompts.py` + `extract.py` (v2 knowledge), `v2.py`,
`threads.py` (thread key ↔ entity), `reconcile.py` (edge status follows
supersede/retire/stale), `code.py` (anchors → file entities), `cli.py`.

**Build:**
- Tables: `entities` (id, project_id, type, key, name, aliases, summary,
  embedding, first_seen, last_seen, status), `entity_edges` (id, project_id,
  from_id, to_id, type, status, first_seen, last_seen, valid_to),
  `edge_evidence` (edge_id, memory_id null, source, episode_id, quote),
  `memory_entities` (memory_id, entity_id, role), `entity_merges`. Idempotent
  DDL, row-level security like the rest, cascade on project delete, included in
  export.
- Deterministic pass on every episode (no model): typed ids, file paths, repo,
  identities, thread keys → entities; anchors → `defined_in`/`part_of` file
  edges.
- Model pass inside v2 knowledge extraction (same call, no extra call): each
  knowledge item also returns `entities: [{type, name}]` and
  `relations: [{from, type, to, quote}]` from the fixed vocabularies; quote must
  be found in the episode like every other quote. Unknown types → `concept` /
  `related_to`.
- Resolution as in "Shared decisions"; fast-model check only for the
  borderline band, batched, and skipped when the project is paused.
- Edge status follows its memories: superseded/retired → `ended` with
  `valid_to`; stale → `stale`; restated → `current` again.
- `memvine-server context-backfill <slug> [--no-model]`: build the graph from
  existing memories (deterministic pass always; model pass only without
  `--no-model`, queued as background jobs so it never blocks live work).
- `GET /v1/context/stats`, `/graph`, `/entities`, `/entities/{id}`, merge,
  split, as specified.

**Done when:** on the sample project (`POST /v1/sample`, v2) the stats show
people, a release, a ticket, threads' entities and at least 6 typed edges with
evidence; "PR 440"/"pr #440" style variants resolve to one entity in a test;
superseding a fact ends the edge it backed; `context-backfill --no-model` on a
copy of real data runs without a model and merges subject variants. Fake LLM
in tests; no real model calls.

**Do not:** change recall ranking, abstention, billing, or v1 behaviour.

### B. The repo the plugin already sees (plugins + server)

**Area:** `plugins/claude-code/bin/memvine-hook.mjs`, `plugins/codex/`,
server ingest of a manifest (`server/memvine_server/context.py` with A, or a
small `manifest.py`), `POST /v1/context/manifest`.

**Build:**
- At session start (at most once a day per repo, hashed, skipped when
  unchanged) the plugin sends a repo manifest, never file contents: repo
  remote, top-level directories, package names (package.json, pyproject,
  go.mod, Cargo.toml, pom.xml, sfdx-project.json), service names from
  Dockerfiles / compose / k8s / Procfile, CODEOWNERS entries, CI tool files,
  env var names from `.env.example`. Opt out like tool outcomes.
- Server turns it into `source: manifest` entities and edges: repo, services,
  modules, `part_of`, `owns` from CODEOWNERS (owner handles → person/team),
  `uses` tool, `configured_by` config.

**Done when:** a test manifest of a small monorepo yields repo → services →
modules with CODEOWNERS ownership; re-sending an identical manifest changes
nothing; plugin tests cover the scan on a temp repo and the once-a-day rule.

### C. Agents query it: `context_query` (server + MCP + SDKs)

**Area:** `server/memvine_server/context_query.py`, `mcp_app.py`, `main.py`,
`sdk/typescript`, `sdk/python`, plugin skill text.

**Build:**
- `POST /v1/context/query`: entity linking (exact key and alias, keyword,
  embedding over entity names/summaries, typed ids in the question), then
  typed traversal 1–2 hops weighted by edge type relevance to the question
  (heuristic map from question words to edge types; no model call by default),
  then facts about the linked entities through recall's own ranking (current
  over superseded; stale flagged), packed into `answer_context` within the
  budget, with "Not known:" lines for asked-about relations that have no edge.
  Abstains like recall when nothing links.
- MCP tool `context_query(question)` returning `answer_context`; tool
  description tells agents to call it before searching code for where/who/what
  questions. SDK methods `context(question)`.
- Billing: same as a recall.

**Done when:** on the sample project, "who owns billing changes?" and "what is
in release 4.2.0?" return the right entities and evidence; an unrelated
question abstains; the MCP tool works in a test.

### D. The console's home is the context graph

**Area:** `console/app/graph/` (replace the fact graph), `console/app/page.tsx`,
new entity panel, `console/lib/*`, mock data.

**Build:**
- Home: the context graph. Nodes are entities colored by domain, sized by
  degree; edges typed and labelled on hover; `ended` edges hidden unless
  "history" is on; `stale` edges drawn broken. Header stats like Uber's
  (entities, edges, entity types, edge types). Left legend: domains with
  counts, click to filter. Top: search entities and types. Draw with Canvas 2D
  (d3-force for layout) so a few thousand nodes stay smooth; dark canvas
  regardless of theme is fine for this view. Pan, zoom, pinch, and a slow
  idle drift that stops on interaction.
- Click an entity: neighborhood focus and a panel: name, type, aliases,
  summary (thread page summary when it has one), relations grouped by edge
  type with trust words and source counts, then the facts about it as
  Receipts with their supersede/contradict history, then sources. Merge and
  split from the panel.
- The fact-graph code (`GraphView`, `NodePanel`) is removed; Receipt, the paste
  box and the sample offer stay. The paste box result shows the entities and
  edges it created.
- Mock data: a realistic project (~150 entities, ~400 edges, every domain)
  following the shapes above exactly.

**Done when:** mock mode shows the graph with every domain and at least 10
edge types, search finds an entity, clicking shows its relations with evidence
and its facts; live against the sample project after A lands.

### E. Docs

Update `docs/how-memory-works.md`, the root README and server README from
"memory graph" to "context graph": entity types, edge types, evidence and
trust, `context_query`, the Uber framing (locate, don't search). Fact-to-fact
edges stay documented as history inside an entity. Mark unbuilt parts
`<!-- landing in workstream X -->`.

### F. Phase 2: GitHub connector (later)

A GitHub App connection per project: PRs (author, reviewers, files, linked
issues, release tags), CODEOWNERS, issues. Deterministic `source: github`
entities and edges, merged with memory's by canonical key. Separate brief when
phase 1 lands.

## Out of scope

Jira/Linear/PagerDuty connectors (phase 3), 3D/WebGL rendering, cross-project
graphs, billing changes.
