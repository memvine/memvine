# memvine

Git-native memory for coding agents. What your agent learns lives in your
repo, travels with the clone, and expires when the code changes.

## Why

Coding agents forget. Claude Code's auto memory stays on one machine and
loads only the first 200 lines of its index at session start. Windsurf keeps
memories per-workspace and its own docs tell you not to rely on them. The
hosted memory layers sync your team's knowledge through a vendor's cloud, on
a subscription.

memvine stores each memory as a markdown file in `.memvine/`, inside the
repo. `git push` shares it with your team. `git clone` onboards a new
machine. Every memory records the commit it was learned at, so when the code
it describes changes, memvine flags the memory stale and your agent
re-checks it instead of repeating something that stopped being true three
merges ago.

There is no database, no embedding index, no daemon, no account, and no API
key. Retrieval works on plain files: scope-filtered BM25 with a token budget,
so it stays fast and its results are explainable.

## Architecture

![memvine architecture](docs/architecture.svg)

## Quickstart

```bash
npm install -g memvine
cd your-repo
memvine init
```

Add it to your agent. For Claude Code:

```bash
claude mcp add memvine -- memvine serve
```

Prefer to run from source (for hacking on memvine)? `git clone`, then
`npm install && npm run build && npm link` puts the `memvine` command on your
PATH.

Memories are plain files under `.memvine/memories/`, but they only reach
another machine once they're committed and pushed — `remember` writes the
file, it doesn't commit for you. If you clone your repo on a second laptop
and memory comes up empty, check that `.memvine/memories/` was committed on
the machine that learned them (`git status` in `.memvine/`). Personal notes
under `.memvine/local/` are gitignored by design and never travel.

Your agent gets four tools: `recall` fetches relevant memories at task
start, `remember` stores knowledge after checking for contradictions,
`revise` updates or retires a memory after re-checking it, and
`check_stale` flags memories whose code has changed.

## Making your agent actually use it

The tools are there, but MCP tools are *opt-in* — the agent calls `remember`
only if it decides to, and agents are trained to finish and stop, not to
journal what they learned. Two levers fix this:

**1. The standing instruction (automatic).** `memvine init` writes a
memvine block into `CLAUDE.md` and `AGENTS.md` telling the agent to `recall`
at the start of every task and `remember` when it finishes one or learns
something durable. These files are loaded into every session, so both Claude
Code (`CLAUDE.md`) and Codex (`AGENTS.md`) see the instruction. Re-run
`memvine compile` to refresh the block after new memories land.

**2. A Stop hook (hard enforcement).** An instruction is a nudge; a hook is
a guarantee. A hook *cannot call an MCP tool itself* — hooks run shell
commands — but a `Stop` hook can block the agent from finishing and hand it
a message, which makes it consider storing before it's allowed to stop. Add
this to `.claude/settings.json` (loop-safe via `stop_hook_active`):

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "input=$(cat); echo \"$input\" | grep -q '\"stop_hook_active\": *true' && exit 0; printf '{\"decision\":\"block\",\"reason\":\"Before finishing: if this session produced any durable knowledge (a fix, a gotcha, a decision, a convention, a runbook), call the memvine remember tool to store it, then stop. If nothing durable was learned, just stop.\"}'"
          }
        ]
      }
    ]
  }
}
```

This is why a hook that ran `memvine serve` or `memvine recall` did nothing
for storage — only the agent knows *what* to remember, so the hook's job is
to re-prompt the agent, not to store anything itself. Codex has no equivalent
Stop hook; there, lever 1 (the `AGENTS.md` instruction) is what you rely on.

## What a memory looks like

```markdown
---
id: mem_7f3a2b9c
kind: semantic
tags: [test, auth]
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

Commit it and every teammate's agent knows it too. Refactor `src/auth/`
and memvine marks it stale for re-checking.

## Memory types

The four kinds copy how cognitive science divides human long-term memory,
because each type needs a different lifecycle:

| Kind | Stores | Example | Staleness |
|---|---|---|---|
| `episodic` | what happened | "Tried Node 22 in March, broke the linter, rolled back" | Never. History stays true. |
| `semantic` | what is true | "Auth uses magic links, chosen over passwords" | Stales when its code changes |
| `procedural` | how to do something | "To deploy: make stage, wait for green, promote" | Stales when its code changes |
| `prospective` | what to do later | "When billing v2 ships, delete the LAUNCH_FLAG hack" | Archived once fulfilled |

An agent picks the kind by asking what sentence it is storing. What
happened is episodic. What's true is semantic. How to is procedural. Do
later is prospective. Domain labels like `test` or `auth` go in freeform
`tags`.

The distinction earns its keep in the staleness engine: refactor
`src/auth/` and the semantic memory "login uses magic links" gets flagged
for re-checking, while the episodic memory "we tried passwordless in March
and support tickets spiked" stays untouched, because history remains true
no matter what the code does now.

## CLI

| Command | Does |
|---|---|
| `memvine init` | Create the `.memvine/` store |
| `memvine add "..." -k semantic -t test auth -s "src/auth/**"` | Add a memory by hand |
| `memvine list` | List memories (`--all` includes retired ones) |
| `memvine stale` | Report memories whose scoped files changed (`--mark` to flag them) |
| `memvine compile` | Render top memories into CLAUDE.md / AGENTS.md |
| `memvine serve` | Run the MCP server |

## Design decisions

**memvine never calls an LLM.** Dedup, contradiction checks, and
re-validation run in the calling agent, steered by the MCP tool
descriptions. The agent that is already running pays for its own thinking.
memvine's code is git commands and file operations.

**Staleness is a git query.** `git diff learned_commit..HEAD` against each
memory's scope, cheap enough to run at every session start.

**Shared and local are separate.** `.memvine/memories/` is committed and
reviewed in PRs like the code it describes. `.memvine/local/` is gitignored
for machine-personal notes. Store no secrets in either; memories are plain
text in your repo.

## Status

v0.2. Retrieval is scope-aware BM25 with a token budget, memories carry a
verified/validated lifecycle, and staleness is measured from the last
confirmed commit. The memory schema may still change before 1.0. Issues and
PRs welcome, see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
