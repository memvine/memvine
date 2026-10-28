---
description: Forget a memory the last session kept (no argument lists them; "last", a number, or a memory id)
---

Run `node "${CLAUDE_PLUGIN_ROOT}/bin/memvine-hook.mjs" forget $ARGUMENTS` and relay its output as it
is. With no argument it only lists what the previous session in this folder kept, numbered; with
`last`, a number from that list, or a memory id, it forgets that memory: it is never recalled
again, and if it had replaced an older fact, the older fact is current again.
