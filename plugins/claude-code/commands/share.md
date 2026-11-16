---
description: Share memories from your last session with the team (no argument lists them; numbers, "all", "last", or a memory id)
---

Run `node "${CLAUDE_PLUGIN_ROOT}/bin/memvine-hook.mjs" share $ARGUMENTS` and relay its output as it is.

With no argument it only lists what the previous session in this folder kept, numbered, with the
quoted words that would go along with each. With numbers (`1 3`), `all` (the suggested ones),
`last` or a memory id, it copies those memories to the team space: the statement, who said it,
when, and that one quoted line. Nothing else of the session leaves the user's own project.

Share only what the user named. Never run this on your own initiative, and never pick for them.
