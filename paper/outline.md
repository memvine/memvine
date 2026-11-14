# Paper outline (working draft, 14 Nov 2026)

A working file: the claim, the evidence already in hand, and what is still missing. Every number
below comes from files under `benchmark/beam/results/` or from the stored benchmark database, and
each is reproducible with the commands in `benchmark/beam/README.md`.

## Working title

*Extracted memories restate the transcript: what a long-conversation benchmark says an agent's
memory should keep.*

## The claim, in three sentences

Memory systems for LLM agents commonly extract "memories" from a conversation and serve them at
recall. On the BEAM long-conversation benchmark we find that sentence-level extraction produces a
lossy copy of the transcript that lowers answer quality, while searching the original turns, with
each turn kept as its own passage, matches a strong search baseline. We characterise what the
extracted store contains, show which question types a memory could still help that search cannot,
and propose that memory hold a small, person-centred profile beside a searchable transcript.

## Contributions (what a reviewer can check)

1. A measurement of what extraction keeps: 806 memories per 100K-token conversation; 52% restate the
   assistant; 53% never served to any benchmark question; 88% have a near-twin; 4% are a preference,
   decision or rule.
2. An ablation on one system, scored with the benchmark's published answer and judge model:
   serving extracted details beside excerpts, excerpts only, and excerpts cut one passage per turn
   with length-normalised keyword ranking.
3. Two retrieval findings that apply to any transcript search: packing turns into fixed-size passages
   buries the person's short turns (about 7 of their turns per briefing against 21), and
   unnormalised keyword rank favours long assistant passages.
4. A failure analysis by question type: where extraction hurt (abstention, knowledge update), and
   where every system loses (multi-session reasoning, contradiction resolution).
5. An observation on grading: a stricter judge reverses the conclusion on summary questions.
6. (If the redesign in `docs/plans/conversation-memory-plan.md` is built and measured) a
   person-centred memory with profile slots, evaluated on held-out conversations.

## Results in hand

BEAM 100K, 15 of 20 conversations, 300 questions, answer and judge `openai/gpt-5.6-luna`, 8,000-token
budget for retrieval arms, one run per arm.

| Arm | Score |
| --- | ---: |
| Whole conversation in the prompt | 0.903 |
| Transcript search, one passage per turn, density-ranked keywords (Memvine now) | 0.892 |
| Plain search baseline (embeddings plus word overlap, one chunk per turn) | 0.889 |
| Transcript search, turns packed into 1,500-character passages, no extracted details | 0.878 |
| Extracted details (a quarter of the budget) plus packed excerpts (Memvine, first version) | 0.869 |

Paired bootstrap over the 300 questions (20,000 resamples), difference in mean score:

| Comparison | Difference | 95% interval |
| --- | ---: | --- |
| Memvine now − plain search | +0.003 | −0.011 to +0.019 |
| Memvine first version − plain search | −0.020 | −0.041 to 0.000 |
| Memvine now − first version | +0.023 | +0.003 to +0.046 |
| Full context − plain search | +0.015 | +0.005 to +0.026 |
| Full context − Memvine now | +0.011 | −0.005 to +0.027 |

What these support: removing extracted details and fixing passage cutting is a real improvement over
the first version; the result is indistinguishable from plain search; full context beats plain search.
What they do not support: any claim that Memvine is better than search.

Per category, the first version lost most on abstention (0.900 against 0.967) and knowledge update
(0.733 against 0.833); the current version scores 1.000 and 0.858 there. All arms are weak on
multi-session reasoning (0.57 to 0.61) and contradiction resolution (0.70 to 0.73).

Also in hand: 500K, three conversations, judged by Claude Sonnet (0.789 against 0.806, not
comparable with published scores); ingestion cost in hours and dollars; worked examples of each
failure (the review-format memory, the deadline replaced by an unrelated deadline).

## What is missing before this is a paper

| Gap | Why it matters | Cost |
| --- | --- | --- |
| The five 100K conversations never ingested | The current recall was tuned on the other fifteen; these are the held-out test | Hours of model calls; under $1 of grading |
| At least one other memory system on the same benchmark | A finding about "extraction" from one system is weak; reviewers will ask | Depends on the system; days |
| More than one run per arm | Answer-model variance is not measured | A few dollars of grading |
| 500K and 1M with the published grader | At 100K the whole conversation fits in a prompt; memory matters where it does not | Ingestion time; tens of dollars for full context |
| The redesigned memory, measured | Turns a negative result into a proposal | Weeks of work |
| Related work | Position against MemGPT/Letta, Mem0, Zep, LangMem, generative agents, and BEAM, LoCoMo, LongMemEval | Reading |

## Sections

1. Introduction: memory as extraction; the question "what should be kept".
2. Background: the benchmark, its question types, published procedure.
3. Systems compared: full context, plain search, extraction plus excerpts, transcript search variants.
4. What extraction keeps: the store analysis, with examples.
5. Results: the table, intervals, per-category breakdown.
6. Why: retrieval analysis (turn packing, keyword rank), failure analysis.
7. What memory is for: the question types search cannot answer; the profile-and-transcript design;
   the mapping to episodic, semantic and procedural memory (Gazzaniga et al.; Atkinson and Shiffrin).
8. Limitations: one benchmark, synthetic conversations, one extraction model, tuning on the test
   conversations, judge sensitivity.
9. Reproducibility: code, commands, cost.

## Venues and rules (checked 14 Nov 2026; confirm on each site before relying on them)

- ICLR 2026 had a workshop on memory for LLM-based agentic systems (MemAgents). Whether it returns
  in 2027 is not announced. ICLR 2027 workshop acceptances are due by 29 Nov 2026, and the
  suggested deadline for workshop papers is 1 Feb 2027.
- arXiv requires a first-time submitter to be endorsed by an established arXiv author in the
  category, for every category since January 2026.
- arXiv has announced bans for authors who submit text they have not checked. The author must be
  able to stand behind every sentence and number.

## Ground rules for this paper

- Posting to arXiv is public disclosure. Any patent decision comes first.
- The numbers above are the only ones to be cited until the gaps are closed. No sentence may say
  Memvine outperforms search.
- The author writes the final text and verifies each claim against the result files.
