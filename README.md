# memvine

**Git-native memory for coding agents.** What your agent learns lives in
your repo, travels with the clone, and expires when the code changes.

Your coding agent relearns your codebase every session. Claude Code's auto
memory stays on one machine. Cloud memory layers sync your team's knowledge
through someone else's servers. memvine does neither:

- **Memory as files.** Every memory is a markdown file in `.memvine/` with
  provenance: what was learned, when, at which commit, about which paths.
  Reviewable in PRs like the code it describes.
- **Git is the sync.** `git push` shares your agent's knowledge with your
  team. `git clone` onboards a new machine. No cloud. No account. No
  subscription. Your memories never leave your infrastructure.
- **Memories that expire.** Every memory knows the commit it was learned at.
  When the code it describes changes, memvine marks it stale and your agent
  revalidates it — instead of confidently telling you something that stopped
  being true three merges ago.
- **Works with your agent.** MCP server for Claude Code, Cursor, Copilot,
  and anything MCP-speaking — plus a compiler that keeps the best of your
  memory inside CLAUDE.md / AGENTS.md budgets for agents that don't.
- **Zero infrastructure.** No database, no embeddings, no daemon, no API
  key. File-based agentic retrieval — the approach that outscored vector
  RAG by ~30 points on [LongMemEval-V2](https://arxiv.org/html/2605.12493v1).

## Architecture

![memvine architecture](docs/architecture.svg)

## Quickstart

```bash
npm install -g memvine
cd your-repo
memvine init
```

Then add it to your agent. For Claude Code:

```bash
claude mcp add memvine -- npx memvine serve
```

That's it. Your agent now has four tools: `recall` (fetch relevant memories
at task start), `remember` (store durable knowledge — checked against
existing memories for contradictions first), `revise` (update or retire a
memory after revalidating), and `check_stale` (flag memories whose code has
changed).

## How a memory looks

```markdown
---
id: mem_7f3a2b9c
kind: gotcha
scope: [src/auth/**]
learned_at: 2026-07-22T21:14:00Z
learned_commit: a1b4c9e
agent: claude-code
status: active
confidence: high
---
The auth integration tests require the local vault container to be started
first (`make vault-dev`), otherwise they fail with connection refused —
this is NOT a flaky test.
```

Commit it, push it, and every teammate's agent knows it too. Refactor
`src/auth/` and memvine flags it stale for revalidation.

## CLI

| Command | What it does |
|---|---|
| `memvine init` | Create the `.memvine/` store in your repo |
| `memvine add "..." -k gotcha -s "src/auth/**"` | Add a memory manually |
| `memvine list` | List memories (`--all` includes retired ones) |
| `memvine stale` | Report memories whose scoped files changed (`--mark` to flag them) |
| `memvine compile` | Render top memories into CLAUDE.md / AGENTS.md digest blocks |
| `memvine serve` | Run the MCP server for your agent |

## Design principles

**memvine never calls an LLM.** Judgment (dedupe, contradiction detection,
revalidation) is done by the calling agent, instructed through MCP tool
descriptions. The agent that's already running pays for its own thinking;
memvine is pure git + file operations. That's how a free tool stays free.

**Personal vs shared.** `.memvine/memories/` is committed and shared;
`.memvine/local/` is gitignored for machine-personal notes. Never store
secrets in either — memories are plain text in your repo.

**Staleness is a git query, not an AI system.** `git diff learned_commit..HEAD`
against each memory's scope. Cheap enough to run at every session start.

## Status

v0.1 — early and moving fast. The memory schema may evolve before 1.0.
Issues and PRs welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
