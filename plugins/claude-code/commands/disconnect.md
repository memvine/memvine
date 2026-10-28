---
description: Stop this folder (and the folders inside it) from sending to Memvine; other folders keep their connection
---

Run `node "${CLAUDE_PLUGIN_ROOT}/bin/memvine-hook.mjs" disconnect $ARGUMENTS` and show the user the
result. Use it in a folder that should not feed the connected project, for example an unrelated
repository under a parent folder that was connected with --here. `--undo` reconnects it.
