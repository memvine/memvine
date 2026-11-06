# Plan: ask the team (judges)

Status: plan, 30 Oct 2026. Issues: #20 (judge), #21 (held queue), #22 (overrule), #23 (principles per person).

## Why

Agents stop and ask the person "A or B?" many times a day. Most of those questions were already
answered by someone on the team, in an earlier session. Today an agent can find the answer only
if it thinks to search memory and reads the result as a ruling.

Spawn ([How I Built the Most AI-Pilled Company in the World](https://x.com/jsnnsa/status/2115623382721126826))
hit the same wall at scale: one morning 700 agent questions were waiting on the CEO. They kept
every chat as text, distilled decisions into per-person principles, and put AI judges in front of
the humans. A judge does one of three things: **decides and cites the precedent**, **sends a
malformed question back** to the agent that filed it, or **holds it for a human**. Judges learn
from corrections. In eight days: 11,000+ verdicts (7,296 decided, 2,213 sent back, 1,863 held);
the CEO answered 27 himself.

Memvine already has the parts: decisions, rules and preferences with evidence that says who said
them (`Evidence.identity`), per-person profiles (0009), standing rules, recall with citations,
Wrong/supersede corrections, and an MCP server. What is missing is the question itself: an agent
asking for a ruling and getting a verdict, or a place in a human's queue.

## Shared decisions

- **Verdicts.** Exactly three, named like this in the API, console and docs:
  `decided` (answer + citations), `sent_back` (what to look up or how to rephrase), `held`
  (waiting for a person).
- **Lean to hold.** A wrong verdict with a citation is worse than a short wait. The judge holds
  when there is no precedent that covers the question, when it is a matter of taste, when
  precedents conflict, or when the action cannot be undone (payments, deleting data, sending to
  customers, production config). Same principle as "too much memory is worse than too little".
- **Sent back** is for questions the code, config, README or ticket can answer, or that are too
  vague to rule on ("what should I do?"). The judge says what is missing.
- **Questions are not memory.** They live in their own table. A `decided` verdict is never
  stored as a memory: it is derived from memories that already exist. Only a person's answer to a
  held question, or a person overruling a verdict, becomes memory (`category=decision`,
  `origin=person`, attributed to that person).
- **Citations** are memory ids plus the evidence quote and who said it, e.g.
  "Use idempotency keys on every Northwind charge retry (Dana, 12 Sep 2026)".
- Extraction is unchanged.

## Data

New migration `0011_questions.sql`:

```
questions(
  id uuid pk, project_id, session_id, asked_by text,      -- identity of the session's person
  question text, context text, options jsonb,
  verdict text check in ('decided','sent_back','held','answered','withdrawn'),
  answer text, citations jsonb,                            -- [{memory_id, quote, identity}]
  answered_by text, answer_memory_id uuid,                 -- set when a person answers
  overruled boolean default false,
  created_at, decided_at, answered_at
)
```

Row-level security like the other project tables (`rls.sql`).

## Workstreams

### 1. `memory_ask` and the judge (decided / sent_back)
- `server/memvine_server/judge.py`: run recall for the question (standing rules, decisions,
  preferences; audiences of the asker), then one model call with a judge prompt that returns
  `{verdict, answer, citations, reason}`. Citations must reference memory ids that were in the
  recalled set; anything else is dropped, and a `decided` with no valid citation becomes `held`.
- MCP tool `memory_ask(question, context?, options?)` in `mcp_app.py`, and
  `POST /v1/questions` in `main.py`. The tool description tells the agent to call it when it
  would otherwise stop to ask the person a decision question, and to follow a `decided` verdict.
- `memvine-hook.mjs ask <question>` and a line in the `memvine` skill.
- Fake model support in tests; judge cases in `benchmark/memory/check.py`: decide with the right
  citation, send back a code question, hold with no precedent, hold an irreversible action even
  with a precedent, hold on conflicting precedents. Run the memory check before shipping.

### 2. Held questions: queue and answers
- Console page "Questions": held questions, newest first, with the asker, the session, the
  context and the closest precedents the judge found. Answer in one sentence.
- An answer becomes a decision memory (through the same path as `memory_remember`, so it is
  reconciled and can supersede), sets `answer_memory_id`, and marks the question `answered`.
- Delivery to the waiting session: `memory_ask(question_id=...)` returns the current state, and
  the session's next recall (`UserPromptSubmit`) and brief carry a short
  "answers to your questions" section for questions that session asked. Nothing is broadcast to
  other sessions: the new decision memory is how everyone else learns it.
- Count of held questions on the console home, next to the review count.

### 3. Overrule: learning from corrections
- Console list of recent `decided` and `sent_back` verdicts with **Overrule**. Overruling asks
  for the right answer, marks the question `overruled`, stores the answer as a decision memory
  (superseding the cited memory when the person says it is wrong), and records a feedback case
  through `review.record_wrong` so it lands in the extraction/judge gate.
- The judge prompt gets the project's recent overrules as extra precedents.
- Track the overrule rate per project; it is the judge's quality number.

### 4. Principles per person (later)
- From profiles and evidence identity, a short page of each person's principles ("Dana: never
  retry a charge without an idempotency key; prefers fixing forward"). The judge can then say
  whose principle it applied and route held questions to the person whose area it is.
- Only after 1–3 work on shared memory with real teams.

## Expectations

Spawn's judges stood on two years of every chat. Memvine keeps few memories on purpose, so expect
a high hold rate at first. That is the right failure: every held question a person answers adds a
real decision, and the hold rate should fall week by week. Measure: verdict mix, overrule rate,
time a held question waits.

## Not doing

- No agent-to-agent broadcast. Spawn found change announcements grew faster than the work and cut
  them; agents coordinated through the shared code, product and database instead. Memory is that
  shared world here.
- No blocking gate on agents. `memory_ask` is advice the agent asked for; a hard approval gate for
  irreversible actions is a separate question.
