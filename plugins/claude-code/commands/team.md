---
description: Connect the team space you share memories with (your sessions stay in your own project)
argument-hint: <team-space-key> [api-url] [--here] | --off
---

Connect a Memvine team space. The arguments given, if any: `$ARGUMENTS`

A team space is a project that holds only what people chose to share. It refuses sessions, so
nobody's conversation can end up there. The user's own project (`/memvine:connect`) keeps taking
their sessions; the team space is read on every recall and written only by `/memvine:share`.

If a team-space key (it starts with `mv_sk_`) is present, run:

```
node "${CLAUDE_PLUGIN_ROOT}/bin/memvine-hook.mjs" team <team-space-key> [api-url] [--here]
```

and relay its output. `--here` connects it for this folder only; `--off` disconnects it; with no
argument it says what is connected. Never repeat the key back, and never write it into a file in
the repository.

If the key is missing, do not ask for it in the conversation. Tell the user to run
`/memvine:team <team-space-key>`, where the key comes from the team's Memvine console under
**API keys** of the team-space project.
