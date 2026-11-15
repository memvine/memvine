# memvine

**Memories grow on the trellis of the code. When the trellis moves, the vine notices.**

Memvine is memory for coding agents and the people who work with them. Your agents talk; Memvine
keeps what stays true (decisions, rules, gotchas, facts, preferences), ties each one to the quote
and the person it came from and to the files it depends on, and briefs the next task with it.
When the code a memory depends on changes, the memory is marked stale instead of being trusted.

![The sample project's memory graph: threads as hubs, facts around them, files at the rim; a superseded launch date, two contradicting rules, and a gotcha whose anchor to src/queue.py is broken because the file changed.](docs/memory-graph.svg)

## Get started

Memvine is a server your agents talk to. Everything below needs a project key (`mv_sk_...`) from one:

- **Run your own.** Docker Compose brings up Postgres, the API and the console, and you bring an
  Anthropic API key (extraction runs on it): [deploy/README.md](deploy/README.md).
- **Use the hosted one.** [app.memvine.dev](https://app.memvine.dev) is open by invitation while the
  project is young. Write to hello@memvine.dev.

Create a project in the console, copy its key, and connect an agent:

| Agent | How |
| --- | --- |
| Claude Code | [`plugins/claude-code`](plugins/claude-code): `/plugin marketplace add memvine/memvine`, `/plugin install memvine@memvine`, then `/memvine:connect mv_sk_... you@company.com [api-url]`. |
| OpenAI Codex CLI | [`plugins/codex`](plugins/codex): one `install` command registers the MCP server and the hooks. |
| Your own agent | The [TypeScript](sdk/typescript) or [Python](sdk/python) SDK, or the HTTP API in [`server/`](server). |
| Any MCP client | `/mcp` on the server: `memory_recall`, `memory_remember`, `memory_session_brief`, `memory_forget`. |

Usage is counted in credits so a project can be capped; nothing is charged. 1 credit per 1,000
characters ingested (re-sending identical text costs none), 0.1 per recall.

## Ten lines

The client libraries are installed from a clone of this repository for now
([TypeScript](sdk/typescript/README.md), [Python](sdk/python/README.md)). With a project key and the sample data loaded (`POST /v1/sample`, or
**Load sample data** in the console: instant, no model call, no credits):

```ts
import { Memvine } from "@memvine/sdk";
const mv = new Memvine({ apiKey: process.env.MEMVINE_KEY!, baseUrl: process.env.MEMVINE_URL });

const s = mv.session("demo-1", { agent: "my-agent", identity: "priya@example.com" });
await s.send("user", "Retries cap at 3: the broker rate-limits us above that.");
await s.send("assistant", "Set MAX_RETRIES = 3 in src/queue.py.");
await s.close();                                   // tending turns the session into memory

const r = await mv.recall("When is the launch?");  // answered from the sample data
console.log(r.briefing);                           // hand this to your model as background
for (const i of r.items) console.log(i.memory.statement, i.previous ? `(was: ${i.previous})` : "");
```

The same in Python, and everything else about how memory is made, connected and forgotten:
**[How Memvine memory works](docs/how-memory-works.md)**.

## Does it work

Two checks, both in this repository and both runnable:

- **Team coding memory (keep little).** The memory check (`benchmark/memory/check.py`) runs real
  model calls over cases of what must be kept, dropped and recalled: 25 of 25. In its agent test,
  Claude Code answered 9 of 9 questions right with Memvine's memory and 0 of 9 without.
- **Conversation memory.** On the [BEAM](benchmark/beam/README.md) long-term memory benchmark at
  100K tokens, scored the published way on 15 of its 20 conversations (300 questions):

  | | Score |
  | --- | ---: |
  | The whole conversation in the prompt | 0.903 |
  | Memvine | 0.892 |
  | Plain search over the raw turns | 0.889 |

  Memvine is level with plain search, not ahead of it, and at this size the whole conversation
  still fits in a prompt. Its first version, which kept every detail as a memory, scored 0.869:
  the kept details restated the conversation and misled the answers. What it does now, why, and
  what conversation memory should become is in
  [the plan](docs/plans/conversation-memory-plan.md). Since that measurement conversation mode also
  keeps a small profile of the person; on three of those conversations (60 questions) two builds scored
  0.885 and 0.867, against 0.863 for plain search: no difference that can be told from chance. Ingesting a conversation into Memvine still
  costs model calls that plain search does not need.

## What it does not do

The full list, with what has and has not been measured, is in
[docs/what-memvine-does.md](docs/what-memvine-does.md). The short version:

- It does not beat a plain search at recalling details from a long conversation; it is level with it.
- It learns from conversation and from what someone adds. It does not read a repository, documents
  or tickets by itself.
- Extraction runs on Anthropic models only, and taking a conversation in costs model calls.
- Nobody outside the project has yet used it on real work for long enough to show it helps a team.

## What a memory is

A short, dated statement with the verbatim quote it came from and who said it ("Priya said",
"seen in a run", "assistant said, unverified", "inferred"). Every project keeps one living page per
thread of work (a release, a PR, a ticket, a branch) and knowledge items of five types: `decision`,
`rule`, `gotcha`, `fact`, `preference`. Memories are joined by named edges:

| Edge | Meaning |
| --- | --- |
| `supersedes` | A newer fact replaces an older one; the old one is kept as history. |
| `contradicts` | Two active facts disagree and neither is newer by evidence. |
| `anchored` | A fact depends on a file at a version; it goes stale when the file changes. |
| `decided_in` | A fact came out of a thread of work. |
| `extends` | A newer fact adds detail to an older one; both stay active. |
| `derived` | A fact inferred from two or more memories, never stated in one place. |

The background work that turns sources into memory is called **tending**. Recall abstains when
nothing is relevant enough, and returns standing rules separately on every call.

## What is in this repository

| Directory | What it is |
| --- | --- |
| `server/` | The memory API and the tending worker (Python, FastAPI, Postgres + pgvector): ingest, extraction with verified quotes, reconciliation, thread pages and knowledge, live sessions, code-change staleness, recall. |
| `console/` | The web console (Next.js): keys, threads, knowledge, memories, sources, sample data, a recall and Ask playground, requests, usage. |
| `plugins/claude-code/`, `plugins/codex/` | The agent plugins: every session starts briefed, and what is said becomes memory. |
| `sdk/typescript/`, `sdk/python/` | Client libraries for the API. |
| `deploy/` | Running your own server with Docker Compose; `deploy/railway/` is how the hosted service runs. |
| `benchmark/memory/` | The memory check: what must be kept, dropped and recalled, run with a real model. |
| `benchmark/beam/` | The BEAM long-term-memory benchmark harness with Memvine, full-context and plain-search backends. |
| `docs/` | [How memory works](docs/how-memory-works.md), [what Memvine can and cannot do](docs/what-memvine-does.md), [security](docs/security.md). |
| `site/` | The memvine.dev homepage. |

Each directory has its own README with how to run it. To work on Memvine itself, see
[CONTRIBUTING.md](CONTRIBUTING.md); to report a security problem, [SECURITY.md](SECURITY.md).

## The earlier git-native CLI

Before the service there was `memvine`, a command-line tool that keeps memory as markdown files
inside a repository, with no server and no model calls. It is a separate tool, still published on
npm, with its source in [`src/`](src): [docs/git-native-cli.md](docs/git-native-cli.md).

## License

[MIT](LICENSE)

