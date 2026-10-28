---
description: Show exactly what Memvine would send from this session that it has not sent yet
---

Run `node "${CLAUDE_PLUGIN_ROOT}/bin/memvine-hook.mjs" cut ${CLAUDE_SESSION_ID}` and show the user
the result. It sends nothing. This is what leaves the machine: the prompts and the replies, with
secrets redacted. Tool results, file contents and diffs are not in it.
