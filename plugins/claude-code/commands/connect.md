---
description: Connect this machine to a Memvine project
argument-hint: <project-api-key> <identity> [api-url] [--here]
---

Connect Claude Code to Memvine. The arguments given, if any: `$ARGUMENTS`

If a project API key (it starts with `mv_sk_`) and an identity (the email the memories are
attributed to) are both present, run:

```
node "${CLAUDE_PLUGIN_ROOT}/bin/memvine-hook.mjs" connect <project-api-key> <identity> [api-url] [--here]
```

Add `--here` when the user wants only this folder (this repository) to use that key; other
folders keep their own settings. Without it, the key becomes the machine-wide default.

and relay its output. Never repeat the key back, and never write it into a file in the repository.

If the key or the identity is missing, do not ask for the key in the conversation. Tell the user to
run `/memvine:connect <project-api-key> <identity> [api-url]`, where the key comes from the Memvine
console under **API keys**, and the API URL is only needed for a self-hosted server.
