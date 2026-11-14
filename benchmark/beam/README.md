# Memvine's BEAM runner

Runs the [BEAM](https://arxiv.org/abs/2510.27246) long-conversation memory benchmark against the
Memvine server, and against two reference arms on the same harness, prompts, models and judge:

| Backend | What answers the question | Why it is here |
| --- | --- | --- |
| `memvine` | the Memvine server's recall, as of the moment asked | the system under test |
| `fullcontext` | every turn up to that moment, verbatim (capped) | the ceiling when the conversation fits the model's context |
| `rag` | top chunks of the raw turns by local embeddings + keyword overlap, same 8,000-token budget | whether synthesis earns its cost over plain retrieval |

The evaluation procedure is the published one, unchanged, so a score from here compares with
other systems' published BEAM scores:

- the dataset as its authors publish it (`dataset.py` downloads it and records the revision);
- ExaBase's answer and judge prompts (`exabase_prompts.py`, SHA-256 checked by `verify.py --prompts`);
- the answer and judge model other published runs use (`llm.py`: `openai/gpt-5.6-luna` via
  OpenRouter, reasoning off);
- the scoring rules (`scoring.py`).

What is Memvine's: the pluggable memory backend and the two reference arms (`memory_api.py`), the
answer coverage check (`coverage.py`), and grading on a local Claude subscription for early rounds
(`BEAM_LOCAL_JUDGE`; real scores, but not comparable with published ones).

The dataset loading, model calls and scoring code started from the MIT-licensed harness that
Past Corp published for its own BEAM run. Credits and licences are in `NOTICE.md` and
`LICENSE-past-harness`.

## Run

```sh
cd benchmark/beam
uv venv --python 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt
export OPENROUTER_API_KEY=...

.venv/bin/python run.py --backend fullcontext --split 100k            # reference arms need no server
.venv/bin/python run.py --backend rag --split 100k

export MEMVINE_API_URL=http://127.0.0.1:8400 MEMVINE_MANAGEMENT_KEY=mv_mk_...   # server running, API-backed LLM
.venv/bin/python run.py --backend memvine --split 100k
.venv/bin/python run.py --backend memvine --split 100k --conversations 2        # a cheap first look
```

Results land in `results/<backend>-<split>/`: `results.jsonl` (one line per question with the
recall response, the answer and the verdicts) and `summary.json` (scores by category, dataset
revision, backend configuration, models). Rerunning the same command skips conversations already
scored. `--reuse TAG` (memvine only) scores again against the projects an earlier run filled.

## Plumbing check without an OpenRouter account

`BEAM_FAKE_MODEL=1` replaces the answer and judge model with canned replies. Every other step is
real: dataset, ingestion, waiting, recall, scoring arithmetic, files. The summary records
`"fakeModel": true` and the scores mean nothing.

```sh
BEAM_FAKE_MODEL=1 .venv/bin/python run.py --backend rag --split 100k --conversations 1
```

## How Memvine is sent the conversation

Consecutive turns of one session go together as one episode, up to `BEAM_EPISODE_CHARS`
(default 6,000) characters, rendered as `## Author · time` blocks, timestamped at the first turn.
One project per conversation, created in Memvine's conversation mode
(`BEAM_MEMORY_MODE`, default `conversation`; `coding` measures the keep-little mode on the same data). Recall asks with `as_of` set to the question's
moment and an 8,000-token budget, and the answer model reads the briefing of the recall response as JSON (see `BEAM_MEMVINE_CONTEXT` below).

## Answer coverage (no model account needed)

`coverage.py` runs recall exactly as the harness does and measures the share of each reference
answer's key terms (numbers, names, content words) present in what comes back. It is a retrieval
proxy, not a BEAM score, but it is free and moves when retrieval moves.

```sh
.venv/bin/python coverage.py --split 100k --conversations 3 --backends rag fullcontext
.venv/bin/python coverage.py --split 100k --conversations 1 --backends memvine --memvine-tag "100k <run tag>"
```

First reading (2026-10-25, 100K, conversations 1-3, 8,000-token budget for rag):

| Category | rag | fullcontext |
| --- | ---: | ---: |
| contradiction_resolution | 0.611 | 0.817 |
| event_ordering | 0.816 | 0.978 |
| information_extraction | 0.860 | 0.925 |
| instruction_following | 0.841 | 1.000 |
| knowledge_update | 1.000 | 1.000 |
| multi_session_reasoning | 0.702 | 0.819 |
| preference_following | 0.678 | 0.906 |
| summarization | 0.624 | 0.890 |
| temporal_reasoning | 0.899 | 0.938 |
| **overall** | **0.781** | **0.919** |
| mean evidence tokens | 7,801 | 121,146 |

## Scored the published way (2026-11-14): 100K, 15 of 20 conversations

Answer and judge model `openai/gpt-5.6-luna` through OpenRouter, the ExaBase prompts and scoring
unchanged, 8,000-token budget for the two retrieval arms. Plain search and full context were run on
all 20 conversations; Memvine has ingested 15, so every column below is over the same 300 questions.

| Category | Full context | Plain search (`rag`) | Memvine, first version | Memvine now |
| --- | ---: | ---: | ---: | ---: |
| abstention | 0.967 | 0.967 | 0.900 | 1.000 |
| contradiction_resolution | 0.721 | 0.708 | 0.733 | 0.704 |
| event_ordering | 1.000 | 1.000 | 1.000 | 1.000 |
| information_extraction | 0.950 | 0.889 | 0.847 | 0.844 |
| instruction_following | 0.983 | 0.967 | 0.942 | 0.983 |
| knowledge_update | 0.833 | 0.833 | 0.733 | 0.858 |
| multi_session_reasoning | 0.610 | 0.586 | 0.566 | 0.586 |
| preference_following | 1.000 | 1.000 | 1.000 | 0.983 |
| summarization | 1.000 | 0.985 | 0.998 | 0.992 |
| temporal_reasoning | 0.967 | 0.950 | 0.967 | 0.967 |
| **overall** | **0.903** | **0.889** | **0.869** | **0.892** |

Over all 20 conversations (400 questions) full context scores 0.915 and plain search 0.899.

What it shows, and its limits:

- **Memvine is level with plain search** (one question in 300 apart) and behind the whole
  conversation in the prompt, which at 100K still fits. It is not ahead.
- **The first version lost to plain search** because it served kept details beside the excerpts.
  About 800 details were kept per conversation, half of them the assistant's own advice restated,
  and they read as things the person had said or done.
- **"Memvine now"** serves the standing instructions and the original words only: one passage per
  turn, keyword hits ranked by density, no kept details (`MEMVINE_RECALL_DETAIL_FACT_SHARE=0`, the
  default). The stored conversations were re-cut with `memvine-server reindex-passages`; nothing
  was ingested again.
- **That change was worked out on these same 15 conversations**, so its score is not a held-out
  result. The five conversations not yet ingested, and the 500K split, are the fair test.
- One run per arm. Ingesting 15 conversations took about a day of model calls on a subscription;
  plain search needs only embeddings. The scoring cost about $19 on OpenRouter for all arms.

The local reading below was graded by Claude Sonnet, which is stricter on summaries: there
Memvine's digests were ahead (0.88 against 0.57), and under the published grader every arm scores
about 1.0 on summaries.

## A local reading (2026-11-12): not a published result

100K, conversations 1-3 (60 questions), 8,000-token budget, both arms answered and judged by Claude
Sonnet on a subscription (`BEAM_LOCAL_JUDGE=sonnet`). The published procedure uses another answer
and judge model, so these numbers are **not comparable** with published BEAM scores; they compare
the two arms with each other, on six questions per category.

| Category | Memvine (conversation mode) | Plain search (`rag`) |
| --- | ---: | ---: |
| abstention | 1.000 | 1.000 |
| contradiction_resolution | 0.667 | 0.646 |
| event_ordering | 1.000 | 0.983 |
| information_extraction | 0.875 | 0.972 |
| instruction_following | 0.917 | 0.917 |
| knowledge_update | 1.000 | 1.000 |
| multi_session_reasoning | 0.688 | 0.646 |
| preference_following | 0.917 | 1.000 |
| summarization | 0.882 | 0.571 |
| temporal_reasoning | 1.000 | 0.958 |
| **overall** | **0.894** | **0.869** |

What it shows: the two are level except on summaries, where digests of the conversation
(`docs/how-memory-works.md`) carry the points a ranking of excerpts misses. Plain search is a little
ahead on information extraction and preferences, by one question's worth. Ingestion took 25 to 60
minutes per 100K conversation on the local model; plain search needs only embeddings.

500K, conversations 1-3 (60 questions, six per category), same setup. Ingestion took about three
and a half hours per conversation.

| Category | Memvine (conversation mode) | Plain search (`rag`) |
| --- | ---: | ---: |
| abstention | 0.750 | 0.667 |
| contradiction_resolution | 0.750 | 0.688 |
| event_ordering | 0.833 | 0.806 |
| information_extraction | 0.729 | 0.833 |
| instruction_following | 0.694 | 0.917 |
| knowledge_update | 0.833 | 0.833 |
| multi_session_reasoning | 0.639 | 0.681 |
| preference_following | 1.000 | 1.000 |
| summarization | 0.746 | 0.632 |
| temporal_reasoning | 0.917 | 1.000 |
| **overall** | **0.789** | **0.806** |

What it shows: at this size the two are level overall (one question in sixty apart; Memvine won
the third conversation 0.846 to 0.837 and lost the first two together 0.761 to 0.790). Memvine is
ahead on summaries, as at 100K, and behind on instruction following. A model-free count of how much
of each reference answer reached the answer model (`coverage.py`) is level between the two in
every category, so the differences elsewhere come from how the answer model read the question,
not from what was retrieved.

The answer model reads Memvine's briefing, the text an agent is given (`BEAM_MEMVINE_CONTEXT`,
default `briefing`). The recall response also carries the same lines as a list; sending both says
everything twice (about 17,600 tokens of context against plain search's 8,700) and scored the same
(0.893).

## Tests

```sh
.venv/bin/pytest -q                       # backends on a synthetic conversation
MEMVINE_API_URL=... MEMVINE_MANAGEMENT_KEY=... .venv/bin/pytest -q   # plus the Memvine round trip
.venv/bin/python verify.py --prompts     # ExaBase prompt file unchanged
```

## Process rules

From the design doc's evaluation plan: pre-register configuration before looking at scores; any
memory error during a run stops it (the harness does: a failed synthesis job raises
`IngestionFailed`); report cost beside accuracy; publish per-category scores with failed
questions counted as wrong, and this runnable harness.
