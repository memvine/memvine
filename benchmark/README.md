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

## Result (8 cases, no model)

```
retrieval recall@3:            100%  (8 in-scope cases)
retrieval recall@5:            100%
avg wrong-memory injection:    25%
avg context tokens:            76
staleness precision:           100%  (TP=6 FP=0)
staleness recall:              100%  (TP=6 FN=0)
unrelated/history false-pos:   0%   (TN=10)
hits rescued cross-scope:      1
```

How to read it:

- **Retrieval precision** was the weak axis at first — the original
  substring-count scorer injected **71%** wrong memory. Scope-aware BM25 with a
  relevance floor and near-duplicate removal cut that to ~**25%** while holding
  the target at rank 1–2 in every case, and roughly halved the recalled
  context. The residual is topically-adjacent memory that only semantic
  similarity would catch — the deferred embedding reranker.
- **Staleness** is kind-aware and clean: semantic/procedural memories flag on
  their evidence edit, while **episodic and prospective memories never flag** —
  history and future intentions don't go stale — so the direct edits on those
  cases are counted as negatives, and all 10 negatives stayed silent (0 false
  positives).
- **Cross-scope recall is now recovered by a capped escape hatch.**
  `crossscope-auth` stores a lesson scoped to `src/auth/**` while the task edits
  `src/api/routes.ts`. Recall stays scope-first (exact-path memories lead), but
  a small number of *strongly* matching cross-scope memories are admitted after
  them, tagged `cross-scope`, so a lesson relevant across files isn't lost —
  this reproduces and fixes the two Xarray misses from the SWE-Bench-CL run. The
  clean in-path cases stayed at 0% injection, so the hatch is additive, not a
  precision regression. Each recalled memory reports its `via=` source
  (exact-path / repo-wide / cross-scope) so retrieval stays explainable.

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

Optional per-case fields:
- `"expectRetrieval": false` — the memory is deliberately out of the edited
  file's scope (a cross-scope lesson). The case is excluded from the retrieval
  aggregate and reported on the "cross-scope (known gap)" line instead.
- The memory's `kind` drives staleness expectations: `semantic`/`procedural`
  must flag on the direct-evidence edit; `episodic`/`prospective` must never
  flag (their direct edit is scored as a negative).

Mix distractor scopes: some repo-wide (`"scope": []`, so they compete on
wording and quality) and some in other paths (so scope filtering should drop
them when `task.path` is set). The first ~20 cases are for debugging the
*evaluation*; expand to 50–100 before making public claims. There are 8 here —
keep going toward 20, adding more cross-scope and near-duplicate cases and at
least one where the stored memory is subtly *wrong* (to test that validation
keeps it out of team recall).

## Notes

- This directory is not shipped to npm (`package.json` `files` is `dist`-only).
- Token counts are a `chars/4` estimate — fine for comparing conditions.
- The harness never mutates memory files (it detects staleness, it doesn't mark
  it), and it cleans up its temp repos.
