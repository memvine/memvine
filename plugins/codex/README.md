# Memvine for OpenAI Codex CLI

Every Codex session starts briefed from your team's memory, and what is said in it becomes
memory. Same server, same key and same behaviour as the Claude Code plugin, including the code
check at session start: a memory `anchored` to a file that changed is shown as "MAY BE OUTDATED".
How memory is made, connected and forgotten: [How Memvine memory works](../../docs/how-memory-works.md).

## Install

Needs Node 18+ on your `PATH` and Codex CLI with hooks (0.155 or later; `codex features list`
shows `hooks  stable  true`). One command, with a project key from the console's **API keys**
screen and the email your memories are attributed to:

```
git clone --depth 1 https://github.com/memvine/memvine ~/.memvine/src && node ~/.memvine/src/plugins/codex/bin/memvine-codex.mjs install mv_sk_... you@company.com
```

Add the API URL as a third argument for a self-hosted server
(`... install mv_sk_... you@company.com https://memvine.internal`). From a checkout you already
have: `node plugins/codex/bin/memvine-codex.mjs install mv_sk_... you@company.com`.

Then one step Codex requires of every new hook: start `codex`, type `/hooks`, and trust the five
memvine hooks. Until you do, Codex skips them (the MCP tools work right away).

If the Claude Code plugin is already connected on this machine, `install` with no arguments
reuses that connection.

What `install` does:

1. Checks the key against the server, then saves it in `~/.memvine/config.json` (mode 600), the
   file the Claude Code plugin uses too.
2. Adds the MCP server to `~/.codex/config.toml`, between `# >>> memvine` and `# <<< memvine`
   marker lines, and nothing else in the file changes. The block holds the key, so the file is
   made readable only by you. A `[mcp_servers.memvine]` table you added by hand is replaced.
3. Adds five hooks to `~/.codex/hooks.json`, after any hooks already there.
4. Copies the hook script to `~/.memvine/codex/`, so the checkout can be deleted afterwards.

Running it again changes nothing; running it with a new key replaces the key. `$CODEX_HOME` is
honoured when set. `--no-hooks` registers only the MCP server.

```
node ~/.memvine/codex/bin/memvine-codex.mjs status      # what is installed, the connection, the last error
node ~/.memvine/codex/bin/memvine-codex.mjs uninstall   # takes out the MCP block, the hooks and ~/.memvine/codex
```

`uninstall` keeps `~/.memvine/config.json`, which the Claude Code plugin shares; delete it to
forget the key.

On a team, the key you install with is your own project's, and the team space is added beside it
(see "Your own project and a team space" in the Claude Code plugin's README; the behaviour is the
same):

```
node ~/.memvine/codex/bin/memvine-codex.mjs team mv_sk_<the team space's key> [api-url]
node ~/.memvine/codex/bin/memvine-codex.mjs share            # lists what the last session kept; share 1 3 | all | last
node ~/.memvine/codex/bin/memvine-codex.mjs unshare last
```

## What it does

| Codex hook | What happens |
| --- | --- |
| SessionStart | Recalls what memory knows about this project and puts it in front of the model. After a compaction or a resume, this session's own working state comes first. Once the previous session in this folder has been tended, shows one line about it ("memvine · last session here: Memory kept 2 decisions and 1 gotcha; 1 fact superseded. To undo one, say "forget the last one" ...") and gives the model the numbered list, so it can forget one. |
| UserPromptSubmit | Recalls memory relevant to the prompt (prompts of 12+ characters). |
| Stop | Sends the turns since the last send to the session's live state, in a detached process. |
| PreCompact | Sends everything pending, so the post-compaction brief is current. |
| SessionEnd | Sends what is pending and closes the session; its turns become long-term memory. A session never ended is closed by the server after 30 idle minutes. |

The MCP server adds `memory_recall`, `memory_remember`, `memory_session_brief` and
`memory_forget` for the model to call itself.

The kept line works as in the Claude Code plugin (see its README, "What memory kept, and
forgetting it"): it comes at the next session start because tending finishes after a session
closes. `memvine-codex forget` lists what the last session kept; `forget last`, `forget 2` or
`forget <memory id>` forgets one (it is never recalled again; a fact it had replaced is current
again).

What leaves the machine: what you typed and Codex's final answers, with keys, tokens and
`*SECRET=` assignments redacted, and the paths of files `apply_patch` changed with their git
hashes (never their content). Codex's progress commentary, its reasoning, tool calls and their
output, the content of files you mention, attached images, and subagent threads (guardian
reviews, spawned agents) stay on the machine.

It reads the rollout file Codex passes as `transcript_path`. OpenAI documents that format as not
a stable interface, so a Codex update can change what is captured; recall and the MCP tools do
not depend on it. `status` shows the last error if sending fails.

Per-folder settings are the Claude Code plugin's: a `projects` entry in `~/.memvine/config.json`
(written by `/memvine:connect ... --here` or `/memvine:disconnect`) applies to Codex sessions in
that folder too, and `deny` lists path fragments never to send. `MEMVINE_API_URL`,
`MEMVINE_API_KEY` and `MEMVINE_IDENTITY` override the file.

## Cost

Sending conversation costs 1 credit per 1,000 characters, and a re-send of identical text is
free; each recall costs 0.1 credit (the hooks recall at session start and on prompts of 12+ characters). The console's
**Usage** page shows both.

## Tests

```
node --test plugins/codex/bin/
```

They run the installer against a temporary `HOME` and `CODEX_HOME` with a stand-in server:
idempotence, other config left untouched, a hand-written table replaced, an unreadable
`hooks.json` refused before anything is written, the hook's output, one send per turn, and
`uninstall` restoring both files.

## Packaging

`package.json` publishes this as the `memvine-codex` npm package (`npm pack` copies the shared
`memvine-hook.mjs` from the Claude Code plugin into `lib/`). Once it is published,
`npx -y memvine-codex install mv_sk_... you@company.com` replaces the clone.
