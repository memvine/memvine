# Coding-agent eval (task-success axis)

The retrieval benchmark answers "did memvine return the right memory?" This
harness answers the harder question: **"does that memory make a fresh coding
agent succeed more often?"** — the number that actually justifies memvine.

It runs the same agent on each task under three conditions, identical model and
settings, changing only the memory in context:

- **none** — issue only.
- **static** — issue + a static `AGENTS.md` holding *every* earlier lesson (plus
  distractors), unfiltered. The honest control: if memvine only ties this, the
  retrieval isn't the differentiator.
- **memvine** — issue + `recall`, scoped to the task's first file.

It reports success per condition **and average context tokens**, because
memvine's bet is *equal success at far less context* (plus staleness, which this
axis doesn't test).

## Wiring check (no model, no Docker)

```bash
npm run build
MEMVINE_EVAL_MOCK=1 node benchmark/agent-eval.mjs
```

The mock agent echoes its context and the mock grader passes iff the case's
`keyFacts` reached the model. This proves the harness routes the right memory
into each condition — it is **not** a task-success measurement. Example output:

```
condition   success  avg ctx tokens
none        0/2      0
static      2/2      123
memvine     2/2      60
```

(none fails without memory; memvine matches static's success at ~half the
context — the hypothesis, on mock data.)

## Real run

1. **Serve a model** on any OpenAI-compatible endpoint (local mlx-lm:
   `mlx_lm.server --model mlx-community/Qwen3-8B-4bit`, or vLLM/Ollama/hosted):

   ```bash
   export MEMVINE_MODEL_BASE_URL=http://localhost:8080
   export MEMVINE_MODEL_NAME=mlx-community/Qwen3-8B-4bit
   node benchmark/agent-eval.mjs
   ```

   This writes `benchmark/out/predictions_<condition>.jsonl` in SWE-bench format
   (`instance_id`, `model_name_or_path`, `model_patch`).

2. **Grade with the official SWE-bench harness** on a Docker host (this repo does
   not grade patches — the container harness is the source of truth):

   ```bash
   python -m swebench.harness.run_evaluation \
     --predictions_path benchmark/out/predictions_memvine.jsonl \
     --run_id memvine --max_workers 4
   # repeat for predictions_none.jsonl and predictions_static.jsonl
   ```

3. **Compare** resolved-rate across the three conditions. Keep model, task order,
   tools, time limit, and token limit identical — the only variable is the memory.

## Cases

Add tasks in `benchmark/agent-cases/*.json`:

```jsonc
{
  "instance_id": "django__django-11433",   // SWE-bench id (grader key)
  "issue": "…the task text…",
  "firstFile": "django/forms/fields.py",    // memvine recall scope
  "dependencyMemories": [ { "body": "…", "kind": "semantic", "scope": ["…/**"] } ],
  "distractorMemories":  [ { "body": "…", "kind": "semantic", "scope": ["…/**"] } ],
  "mock": { "keyFacts": ["token that proves the dependency was used"] }
}
```

`dependencyMemories` are the earlier lessons a prior agent would have stored;
`distractorMemories` are unrelated memories that must NOT crowd the context —
they're what separates memvine (filters) from static (dumps everything).
