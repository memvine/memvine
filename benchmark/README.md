# memvine coding-memory benchmark

A small, deterministic benchmark for the thing memvine claims to be best at:
**recovering trustworthy, repository-specific knowledge, and knowing when the
code supporting it has changed.** MemoryArena tests conversational and math
memory; this tests memory *inside a Git repo*.

## What it measures

Each case is a tiny fixture repo plus one memory worth keeping, a set of
**distractor** memories, and two follow-up edits. Per case the harness checks:

1. **Retrieval selection** — with the store salted with distractors, does
   `recall` return the one relevant memory in top-k (recall@3, recall@5), and
   how much irrelevant memory rides along (wrong-memory **injection rate**)?
   A store of size 1 makes retrieval trivial, so distractors are mandatory.
2. **Staleness signal** — change the memory's **evidence** file → it must flag
   "needs revalidation" (true positive). Change an **unrelated** file → it must
   stay silent (false positive). Aggregated into staleness **precision /
   recall** and an **unrelated-edit false-positive rate**.
3. **Context size** — tokens `recall` would inject, so "help" is never bought
   with unbounded context.

Axes 1–3 need **no model** and run in a second. A fourth axis — *does a fresh
agent complete the task better with the memory?* — needs an LLM and a
**static-`AGENTS.md` control** (same fact, plain doc); if memvine only ties the
static doc on that axis, the retrieval isn't the differentiator, the staleness
is. It's stubbed in `adapters/model.mjs` for you to wire to your local model.

## Run

```bash
npm run build
node benchmark/run.mjs
```

## Seed result (3 cases, no model)

```
retrieval recall@3:            100%
retrieval recall@5:            100%
avg wrong-memory injection:    71%
avg context tokens:            152
staleness precision:           100%  (TP=3 FP=0)
staleness recall:              100%  (TP=3 FN=0)
unrelated-edit false-positive: 0%
```

Read this as a baseline, not a victory. **Staleness is already the strong
axis** — it fires on the evidence edit and stays silent on the unrelated one,
every time. **Retrieval precision is the weak axis**: 71% injection, because
the lexical scorer admits any memory that shares a single query term. That's
the actionable finding — the next retrieval work (BM25, near-duplicate removal,
a minimum-score floor) should target *precision*, not staleness, and this
harness is how you'll know if it worked.

## Adding cases

Drop a JSON file in `cases/`. Shape:

```jsonc
{
  "name": "short-slug",
  "files": { "src/area/file.ts": "…" },      // fixture repo contents
  "memory": {                                  // the memory worth keeping
    "body": "…", "kind": "semantic",           // episodic|semantic|procedural|prospective
    "scope": ["src/area/**"], "confidence": "high"
  },
  "task": { "query": "…", "path": "src/area/file.ts" },  // path optional
  "distractors": [ { "body": "…", "kind": "semantic", "scope": ["src/other/**"] } ],
  "edits": {
    "directEvidence": { "src/area/file.ts": "…changed…" },  // must flag stale
    "unrelated":      { "src/other/file.ts": "…changed…" }  // must NOT flag stale
  }
}
```

Mix distractor scopes: some repo-wide (`"scope": []`, so they compete on
wording and quality) and some in other paths (so scope filtering should drop
them when `task.path` is set). The first ~20 cases are for debugging the
*evaluation*; expand to 50–100 before making public claims.

## Notes

- This directory is not shipped to npm (`package.json` `files` is `dist`-only).
- Token counts are a `chars/4` estimate — fine for comparing conditions.
- The harness never mutates memory files (it detects staleness, it doesn't mark
  it), and it cleans up its temp repos.
