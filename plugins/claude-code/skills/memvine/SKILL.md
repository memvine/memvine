---
name: memvine
description: Search this project's memory in Memvine — earlier decisions, why something was built a certain way, what was tried before, who prefers what. Use when the user asks what was decided, whether something was attempted already, or why the code looks the way it does, and the answer is not in the working tree.
---

# Searching Memvine

Memvine holds what earlier sessions on this project learned: decisions and their reasons,
conventions, pitfalls, preferences, and what was tried and rejected. The repository says what
the code is; Memvine says why it became that.

## When to search

- "What did we decide about X?" · "Have we tried this before?" · "Why is this built this way?"
- Before changing a surprising constant, workaround, or disabled check.

Do not search for what the working tree answers; read the files for those.

## How

```
node "${CLAUDE_PLUGIN_ROOT}/bin/memvine-hook.mjs" search <the user's question, in their own words>
```

Pass the question as asked; recall runs on meaning.

## Using what comes back

Each line is one fact, led by what it is, its date and its edges: `[decision 15 Oct, supersedes
6 Oct]` replaced an older value (shown as "was: ..."); `contradicts` means another statement
disagrees and neither replaced the other, so ask which is right; `extends` adds detail to an older
fact; "inferred, derived from 3 memories" was concluded, never said; "assistant said, unverified"
is an assistant's claim; `anchored src/x.py, MAY BE OUTDATED` means that file changed since, so
read the code. When memory disagrees with the code, the code wins, and it is worth telling the
user the two disagree. If nothing comes back, say so; do not fill the gap with a guess.

If the user says a memory is wrong or should be forgotten, run
`node "${CLAUDE_PLUGIN_ROOT}/bin/memvine-hook.mjs" forget` to list what the last session kept, then
`... forget <number>` (or `forget <memory id>`), and tell them what was forgotten.
