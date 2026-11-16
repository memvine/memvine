---
description: Take back a memory you shared with the team (no argument lists them; a number, "last", or a memory id)
---

Run `node "${CLAUDE_PLUGIN_ROOT}/bin/memvine-hook.mjs" unshare $ARGUMENTS` and relay its output as it is.

With no argument it lists what this machine shared from the last session. With a number, `last`
or a memory id it takes that one back: the team's copy is retired when this share created it, and
left alone when the team already had that memory from someone else. The user's own copy stays.
