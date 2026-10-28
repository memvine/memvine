# How Memvine memory works

**Memories grow on the trellis of the code. When the trellis moves, the vine notices.**

![The sample project's memory graph: threads as hubs, facts around them, files at the rim. The launch date supersedes an older one, two weekend rules contradict each other, and a gotcha anchored to src/queue.py is drawn with a broken edge because the file changed.](memory-graph.svg)

*The sample project's graph, drawn by hand in the console's style. The sample itself has no code
anchors; the retries gotcha on `src/queue.py` (and the fact that extends it) is added to show what
a stale anchor looks like.*

Your agents talk. Memvine reads what they say, keeps the parts that stay true, ties each one to
where it came from, and hands the relevant ones back at the start of the next task. A memory is a
short statement (a decision, a rule, a gotcha, a fact, a preference) with a date, a speaker, the
exact quote it came from, and edges to the memories and files it depends on.

## Ten lines

You need a project key (`mv_sk_...`, from the console's **API keys** screen, or
`memvine-server create-project` on your own server) and, to see an answer straight away, the
sample data: the console offers **Load sample data** on an empty project, or

```sh
curl -X POST "$MEMVINE_URL/v1/sample" -H "Authorization: Bearer $MEMVINE_KEY"
```

It loads instantly, with no model call and no credits. Then, in TypeScript (`@memvine/sdk`):

```ts
import { Memvine } from "@memvine/sdk";
const mv = new Memvine({ apiKey: process.env.MEMVINE_KEY!, baseUrl: process.env.MEMVINE_URL });

const s = mv.session("demo-1", { agent: "my-agent", identity: "priya@example.com" });
await s.send("user", "Retries cap at 3: the broker rate-limits us above that.");
await s.send("assistant", "Set MAX_RETRIES = 3 in src/queue.py.");
await s.close();                                   // tending turns the session into memory

const r = await mv.recall("When is the launch?");  // answered from the sample data
console.log(r.briefing);                           // hand this to your model as background
for (const i of r.items) console.log(i.memory.statement, i.previous ? `(was: ${i.previous})` : "");
```

Or in Python (`memvine-sdk`):

```python
import os
from memvine_sdk import Memvine
mv = Memvine(os.environ["MEMVINE_KEY"], base_url=os.environ.get("MEMVINE_URL", "https://api.memvine.dev"))

s = mv.session("demo-1", agent="my-agent", identity="priya@example.com")
s.send("user", "Retries cap at 3: the broker rate-limits us above that.")
s.send("assistant", "Set MAX_RETRIES = 3 in src/queue.py.")
s.close()                                    # tending turns the session into memory

r = mv.recall("When is the launch?")         # answered from the sample data
print(r.briefing)                            # hand this to your model as background
for i in r.items: print(i["memory"]["statement"], f"(was: {i['previous']})" if i["previous"] else "")
```

Recall prints the current launch date with the old one beside it, because the newer statement
superseded the older one. Two more things recall does that matter on day one:

- **It abstains.** If nothing in memory is relevant enough to the question, `abstained` is true and
  the briefing is empty, so an unrelated question never gets a confident-looking wrong answer.
- **Standing rules come separately.** Rules marked standing ("Never deploy on Fridays") come back
  in `standing` on every recall, abstained or not, and head the briefing when it is served.

The session you just sent becomes memory in the background (see Tending); once `GET /v1/jobs`
shows nothing pending, "How many retries?" finds it.

With edges, each recalled memory also carries its neighbours, and any memory's full neighbourhood
is one call away:

```ts
const r = await mv.recall("When is the launch?", { include: { related: true } });
for (const e of r.items[0].related) console.log(e.kind, e.direction, e.statement); // supersedes out "Launch is 29 Oct"
const node = await mv.related(r.items[0].memory.id);   // the node plus every edge, both directions
```

Agents in Claude Code and Codex need no code at all: the plugins in `plugins/claude-code` and
`plugins/codex` brief every session from memory and send what is said.

## Sources and memories

|  | Sources | Memories |
| --- | --- | --- |
| What | The raw text you send: a conversation, a live session's messages, a document. Stored as sent (secrets redacted). | Short, dated statements drawn from sources, each with the verbatim quote it came from. Organized as thread pages for work in progress (one per piece of work) and a small set of durable knowledge items. |
| Examples | A Claude Code session; a standup transcript; a PR thread pasted in. | "Launch is 12 Nov" (decision); "Never deploy on Fridays" (standing rule); "Retries cap at 3" (gotcha, anchored to `src/queue.py`). |
| Who changes it | You. Re-sending an id with the same text is free and changes nothing; new text replaces the source and everything drawn from it. | Tending writes and reconciles them. People confirm one (still true), retire one (forget), or state a correction, which supersedes the old one. |
| Lifecycle | Kept until the project's `retention_days`, then the raw text is dropped and the memories stay. Deleting a source deletes the memories drawn only from it. | Active until superseded (kept as history), stale (its file changed), or retired. |

## The edges

Memories are connected, and the connections have names, spelled the same way in the API,
the console and these docs.

| Edge | Meaning |
| --- | --- |
| `supersedes` | A newer fact replaces an older one; the old one keeps its end date. |
| `contradicts` | Two active facts disagree and neither is newer by evidence. |
| `anchored` | A fact depends on a file at a version; it goes stale when the file changes. |
| `decided_in` | A fact came out of a thread of work. |
| `extends` | A newer fact adds detail to an older one; both stay active. |
| `derived` | A fact the model inferred from two or more memories, never stated in one place. |

**`supersedes`.** On 9 Oct Priya says "launch is 29 Oct"; on 21 Oct she says "we're moving the
launch to 12 Nov". "Launch is 12 Nov" supersedes "Launch is 29 Oct", and recall answers with the
new date and shows the old one as `(was: ...)`.

**`contradicts`.** In two sessions on the same day, Sam says "retries cap at 3" and Dana says
"retries cap at 5". Neither is newer by evidence. Both stay active and recall shows them together, so the agent asks
instead of picking one.

**`anchored`.** "Retries cap at 3" was learned in a session that edited `src/queue.py` at
`a1b4c9e`. Someone changes that file; the fact is marked stale and the next brief shows it as
"[gotcha 3 Oct, anchored src/queue.py, MAY BE OUTDATED] Retries cap at 3 (src/queue.py changed
since ...; check the code)", so the agent reads the code instead of trusting the memory.

**`decided_in`.** "Billing is manual for the beta" was decided in the thread for release 0.4. The
thread page lists it under Decisions, and an agent working on that release gets the page and the
decision together.

**`extends`.** "The cap exists because the broker rate-limits" extends "Retries cap at 3". The
second adds the reason without replacing the rule, so both stay active.

**`derived`.** Three sessions show Priya approving partner contracts, answering partner questions
and owning the partner checklist. "Priya owns partner onboarding" is derived from them: stored as
inferred, at low confidence, with links to the memories it came from.

Two more things read like edges in the picture but are fields on the memory: **said by** (who said
it, and whether it was seen, stated, claimed or inferred) and **why** (the reason given for a
decision or rule).

Today the first four are fields on every memory (`supersedes` and `superseded_by`,
`conflicts_with`, `stale_reason` with the anchors behind it, `thread_id`).

All six are served as named edges: `GET /v1/memories/{id}/related` returns a memory and every edge
in both directions, `POST /v1/recall` with `include: {related: true}` adds up to six edges to each
recalled memory, and `GET /v1/graph` returns a neighbourhood of facts, threads, files and people
for the console's graph. `extends` and `derived` are written by tending.

The agent sees the same graph. Each line of a recall briefing names its edges in these words,
with the date of the memory at the other end, and quotes that memory where the line needs it:

    [decision 21 Oct, supersedes 9 Oct] Launch is 12 Nov (was: Launch is 29 Oct)
    [rule 21 Oct, contradicts 21 Oct] Retries cap at 5
    [fact 22 Oct, extends 21 Oct] The cap exists because the broker rate-limits (adds to: Retries cap at 3)
    [fact 23 Oct, inferred, derived from 3 memories] Priya owns partner onboarding
    [gotcha 3 Oct, anchored src/queue.py, MAY BE OUTDATED] Retries cap at 3 (src/queue.py changed since 20 Oct 2026; check the code)

A contradiction is quoted once, in a pair at the top of the briefing that asks the agent to say
both and ask which is right; the lines themselves only name it. Trust is said in words where it is
lower than a person or a run: "assistant said, unverified" or "inferred".

### How a memory says who said it

Wherever a memory is shown to a person, its `assertion` reads as words, not a badge:

| `assertion` | Reads as |
| --- | --- |
| `observed` | seen in a run |
| `stated` | the speaker's name: "Priya said" |
| `claimed` | assistant said, unverified |
| `inferred` | inferred |

An assistant's report on its own work ("all tests pass") is always `claimed`, and counts for half
in recall.

## One transcript becomes many facts

This is the first source in the sample project, a six-line kickoff:

```
## Priya · 5 Oct 10:00
Target public launch is 29 Oct.
Refunds over $500 always need a human approver; the support bot may only draft them.
And never deploy on Fridays. I prefer async review over meetings.
## Agent · 5 Oct 10:01
Noted: launch on 29 Oct, refunds above $500 go to a human approver, no Friday deploys, and reviews happen async.
```

It yields four memories, each with the quote that proves it:

| Type | Memory | Quote |
| --- | --- | --- |
| decision | The public launch is planned for 29 Oct. | "Target public launch is 29 Oct." |
| rule | Refunds over $500 always need a human approver; the support bot may only draft them. | "Refunds over $500 always need a human approver" |
| rule, standing | Never deploy on Fridays. | "never deploy on Fridays" |
| preference (low confidence: said once) | Priya prefers async review over meetings. | "I prefer async review over meetings." |

The agent's line adds nothing: it restates what Priya said. A quote that cannot be found in the
source word for word is dropped, so a memory cannot cite something nobody said. A week later,
"Legal pushed back on the refund copy, so we're moving the launch from 29 Oct to 12 Nov" becomes a
new decision that supersedes the first, with its reason as its `why`.

In the console, an empty project opens on a paste box: paste a few lines from a session, a standup
or a PR thread and the facts appear one by one with their edges, within the request
(`POST /v1/ingest` with `instant: true`, for one source under 4,000 characters, billed like any
other).

## Tending

Turning sources into memory runs in the background. Memvine calls this **tending**.

1. **As messages arrive.** Each message of a live session is stored the moment it is sent. Other
   sessions of the project see it straight away, as pending, before anything is extracted.
2. **When a stretch is sealed.** A session is cut into stretches at a completed exchange once
   about 6,000 characters have built up, once the oldest unsealed message is 10 minutes old, and
   when the session closes (or after 30 idle minutes). A source sent with `ingest` is one stretch.
3. **Extraction and reconcile.** For each stretch, one model call rewrites the thread pages it
   touches and a second proposes knowledge items, each with a verbatim quote. Each candidate is then compared with what is
   already known: the same thing (evidence is added), an update (`supersedes`), a contradiction
   (`contradicts`, or `supersedes` when the new evidence is more recent), or something new.
4. **Code checks.** At the start of each session the Claude Code and Codex plugins report the
   current versions of anchored files (`POST /v1/code/check`; any client can call it); memories whose files changed turn stale, and turn active again if the file
   changes back.

A memory is visible to recall as soon as its stretch's job is done: `GET /v1/jobs` shows the queue
(the console's **Jobs** page shows the same), and the pending count reaching zero means everything
sent so far is memory.

<!-- landing in workstream 2 -->
The console's header says what tending is doing: "tended 4 min ago · tending 2 sessions".
<!-- /landing in workstream 2 -->

## Memory types and their behavior

| Type | Behavior | Status |
| --- | --- | --- |
| decision | Persists; carries a `why`; `decided_in` a thread. | true today |
| rule | Persists; may be `standing` (always on, every recall). | true today |
| gotcha | Anchored to code; goes stale when the file changes. | true today |
| fact | Persists until superseded; history kept. | true today |
| preference | Strengthens with repetition: each restatement raises confidence. | true today |
| progress | Never a fact. Updates the thread page, then expires. | true today |

A preference said once starts at low confidence and rises one step (low, medium, high) each time
it is restated, with each restatement's quote kept as evidence. Progress is refused as knowledge,
and the State and Next steps items it leaves on a thread page are dropped when the page is next
rewritten, if their newest source is more than 30 days older than the one being written.

## Forgetting

Memory forgets in four ways:

1. **The code changed.** A memory anchored to a file goes stale when the file changes. It is not
   deleted: recall still shows it, marked "MAY BE OUTDATED", so the agent checks the code. Restating
   or confirming it re-anchors it to the current version. History ("we tried X on Tuesday") is never
   anchored, because it stays true whatever the code does.
2. **It was superseded.** The old value gets an end date and leaves the answer, but stays as
   history: recall shows it as `(was: ...)` beside the new one, and serves it on its own only when
   the question asks about the past or passes `as_of`.
3. **Retention.** A project with `retention_days` drops the raw text of older sources and keeps
   the memories drawn from them. Deleting a source removes the memories drawn only from it; if a
   superseded value's successor goes with it, the old value becomes current again. Retiring a
   memory (`PATCH /v1/memories/{id}` with `{"status": "retired"}`, `retire` in the SDKs, MCP
   `memory_forget`) takes it out of recall; if it had superseded an older fact, that fact is
   current again. The plugins open each session with what the previous one kept ("Memory kept 2
   decisions and 1 gotcha; 1 fact superseded.", from `GET /v1/sessions/{id}/kept`), and "forget
   the last one" retires it.
4. **It was refused as noise.** Progress events ("ran the tests"), status snapshots, plans nobody
   accepted, debugging guesses and tooling trivia never become memories at all. Progress belongs on
   the thread page, not in knowledge.

## What you don't do

- **No schema.** You send text; tending decides what is a decision, a rule or a gotcha.
- **No tags.** Threads come from branches, PR numbers, ticket ids and release versions in the
  text; subjects come from the statements.
- **No pruning.** Superseded, stale and noisy memories leave the answer by themselves, with their
  history kept.
- **No prompt engineering.** Recall returns a briefing written to be handed to a model as is, sized
  to the token budget you give it.

Connect once: a key, the plugin or ten lines of SDK, and memory keeps itself.

## Cost

1 credit per 1,000 characters ingested (re-sending identical text is free), 0.1 credit per recall.
Sample data costs nothing. The console's **Usage** page shows both.
