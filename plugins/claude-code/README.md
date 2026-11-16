# Memvine for Claude Code

Every session starts briefed from your team's memory, and what is said in it becomes memory.

```
/plugin marketplace add memvine/memvine
/plugin install memvine@memvine
/memvine:connect mv_sk_... you@company.com [api-url]
```

## What it does

| Hook | What happens |
| --- | --- |
| SessionStart | Recalls what memory knows about this project and puts it in front of the model. After a compaction or resume, this session's own working state comes first (goal, decisions, open questions, what is in play), then relevant history. Shows what the previous session here kept (see below). |
| UserPromptSubmit | Recalls memory relevant to the prompt (prompts of 12+ characters). |
| Stop | Sends the turns since the last send to the session's live state on the server, in a detached process. |
| PreCompact | Sends everything pending, so the post-compaction brief is current. |
| SessionEnd | Sends what is pending and closes the session; its turns become long-term memory. A session that is never ended is closed by the server after 30 idle minutes. |

What leaves the machine: your prompts and the assistant's replies, with keys, tokens and
`*SECRET=` assignments redacted, and the paths of files the session's tools read or edited with
their git hashes (never their content). Between the replies goes one compact `[tools]` line per
stretch of tool work, so memory can keep lessons the session proved rather than only what the
assistant said: which files were read or searched, each command's gist (setup like `cd` dropped,
temp paths as `<tmp>`), whether it failed, and for a failure or a test/build/lint run a short
redacted tail of its output (at most 300 characters each, 1,200 per line). For example:

    [tools] Read ok: src/export.py; Bash `uv run pytest tests/test_export.py -q` failed (exit 1): E psycopg.OperationalError: connection refused on port 5432 1 failed in 0.4s

Full tool output, file contents, diffs and subagent threads stay on the machine. To send no tool
lines, set `"toolOutcomes": false` in `~/.memvine/config.json` (or `MEMVINE_TOOL_OUTCOMES=0`).
`/memvine:cut` shows exactly what is pending for the current session.

The brief names each fact's edges in the memory graph's own words, so the agent sees what the
console draws:

    [decision 15 Oct, supersedes 6 Oct] The public launch moved to 14 Nov because legal pushed back on the refund copy (was: The public launch is planned for 31 Oct)
    [rule 26 Oct, contradicts 24 Oct] Weekend escalations page the on-call engineer right away
    [rule 21 Oct, extends 21 Oct] Only Priya merges release work (adds to: Release 4.2.0 ships ...)
    [fact 20 Oct, inferred, derived from 3 memories] Priya owns partner onboarding
    [gotcha 3 Oct, anchored src/queue.py, MAY BE OUTDATED] Retries cap at 3 (src/queue.py changed since 3 Oct 2026; check the code)

Memories about code go stale when the code changes: a fact that names a file the session touched
is `anchored` to that file's version, and each new session checks the anchored files first. If one
changed, the brief marks the memory "MAY BE OUTDATED" and says which file changed, so the agent
checks the code instead of trusting it. History ("we tried X on Tuesday") never goes stale.
Turning what was said into memory (tending) runs on the server after each send; the console's
**Jobs** page shows it. How memory is made, connected and forgotten:
[How Memvine memory works](../../docs/how-memory-works.md).

### What memory kept, and forgetting it

When a session closes, the next session started in the same folder opens with one line for you:

    memvine · last session here: Memory kept 2 decisions and 1 gotcha; 1 fact superseded. To undo one, say "forget the last one" (or name it).

Why at the next start and not at the end: the session's conversation becomes memory on the server
after it closes (tending takes from seconds to a few minutes), and a SessionEnd hook has nothing
left to print on. So SessionStart asks the server (`GET /v1/sessions/{id}/kept`) what the previous
session in this folder added: memories first drawn from it, not ones it only restated. While
tending is still running nothing is shown and the next start asks again; each session's line is
shown once, and only when it kept something. Sessions older than a week, and backfilled ones, are
not reported.

The agent gets the same list, numbered, so "forget the last one" (or "forget the launch date")
works: it runs `memvine-hook.mjs forget <number>`, which retires that memory on the server
(`PATCH /v1/memories/{id}` with `status: retired`). A forgotten memory is never recalled again,
and if it had replaced an older fact, that fact is current again. You can do the same yourself:
`/memvine:forget` lists the last session's memories, `/memvine:forget last` or
`/memvine:forget 2` forgets one, and `/memvine:forget <memory id>` forgets any memory.

### Your own project and a team space

`/memvine:connect` names the project your sessions go to: your own. On a team, add the team space:

```
/memvine:team mv_sk_<the team space's key> [api-url] [--here]
```

A team space is a project that refuses sessions and holds only what people shared; the command
refuses a key whose project takes sessions, so a conversation cannot end up where teammates read.
After it:

- Your sessions keep going to your own project and nowhere else.
- Every brief and every prompt is answered from both memories; the team's part comes under its
  own heading, and a memory in both is said once.
- The line about what the last session kept now ends with what could be shared ("1 of them could
  be shared with the team"). `/memvine:share` lists the memories with the quoted words that would
  go along; `/memvine:share 1 3`, `/memvine:share all` (the suggested ones) or `/memvine:share last`
  copies them to the team space with who said each and when. The agent is told never to share on
  its own.
- `/memvine:unshare` takes one back.
- When a fact you shared changes, the new memory is listed as "(suggested: replaces what you
  shared)", and sharing it replaces the team's copy of the old one.

If the team space is set to share automatically, the suggested memories are copied at the next
session start without asking; a personal preference is never suggested. `/memvine:team --off`
disconnects the team space.

Commands: `/memvine:connect`, `/memvine:team`, `/memvine:share`, `/memvine:unshare`,
`/memvine:status`, `/memvine:cut`, `/memvine:backfill`, `/memvine:disconnect`, `/memvine:forget`.
The `memvine` skill lets the model search memory itself for "why is this like this" questions.

`/memvine:connect <key> <identity> [api-url] --here` binds only this folder (this repository) to
that key; without `--here` the key is the machine-wide default. `/memvine:disconnect` stops this
folder and the folders inside it from sending or recalling, whatever the machine is connected to;
`/memvine:disconnect --undo` reverses it.

`/memvine:backfill` imports this repository's earlier Claude Code sessions, so memory starts with
its history. It first lists them (dates, turns, size, credits) and sends only after you agree;
`--days 30` limits it to recent ones. Sessions already sent live are skipped, and the same
filtering and redaction apply.

Configuration lives in `~/.memvine/config.json` (`apiUrl`, `apiKey`, `identity`, `team`: the team
space's `apiUrl` and `apiKey`, `deny`: a list
of path fragments for projects never to send; `projects`: the per-folder bindings `--here` and
`/memvine:disconnect` write). Environment variables `MEMVINE_API_URL`, `MEMVINE_API_KEY` and
`MEMVINE_IDENTITY` override it.

Cost: sending conversation costs 1 credit per 1,000 characters, and a re-send of identical text
is free; each recall costs 0.1 credit (the hooks recall at session start and on prompts of 12+ characters).

## Tests

```
node --test plugins/claude-code/bin/
```

## Other agents

**Codex CLI** has hooks too: `plugins/codex/` installs the same briefing and capture with one
command, and registers the MCP server. See [plugins/codex/README.md](../codex/README.md).

Agents without a hook system connect through the server's MCP endpoint (`/mcp`, streamable
HTTP) with a project key. They recall and store when the model decides to, rather than on every
turn, so the instructions below ask them to.

**Cursor** (`.cursor/mcp.json` in the repository, or the global one):

```json
{ "mcpServers": { "memvine": { "url": "https://api.memvine.dev/mcp", "headers": { "Authorization": "Bearer mv_sk_..." } } } }
```

Then add to the agent's instructions (AGENTS.md, .cursor/rules): *"Before acting on anything that
depends on an earlier decision, a person, a date or a value, call memory_recall with the question.
When a decision is made or a rule is agreed, call memory_remember with one self-contained sentence."*
`memory_forget` retires a memory by id when the user says it is wrong.

For a custom agent, use the Python or TypeScript SDK in `sdk/`, which also streams live sessions.
