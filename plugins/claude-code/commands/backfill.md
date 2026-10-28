---
description: Send this repository's earlier Claude Code sessions to Memvine, so memory starts with its history
---

1. Run `node "${CLAUDE_PLUGIN_ROOT}/bin/memvine-hook.mjs" backfill --skip ${CLAUDE_SESSION_ID} $ARGUMENTS`.
   This sends nothing: it lists the earlier sessions of this folder that were never sent, with
   their size and the credits sending them would cost.
2. Show the user that list and ask whether to send them. Mention that only prompts and replies
   leave the machine, with secrets redacted, and that `--days N` limits it to recent sessions.
3. Only if the user agrees, run the same command with `--send` added, and show the result.
