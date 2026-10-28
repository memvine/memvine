"""Prompts for the synthesis pipeline.

The extraction rules encode what the Memvine benchmark runs taught us: a memory
is worth keeping only if it is one self-contained fact with a quotable source,
and an agent's report about its own progress is a claim, not a fact.
"""
import json

RECONCILE_SYSTEM = """You compare a NEW memory item with the EXISTING items it might relate to, and decide how it relates to the one it matches best.

Return ONLY a JSON object: {"match": <number of the EXISTING item, or null>, "relation": "same" | "update" | "contradiction" | "extends" | "different", "reason": string}
Use "match": null with "relation": "different" when the NEW item matches none of them.

- same: they state the same fact with the same value (wording or detail may differ).
- update: the same attribute of the same thing, and the NEW item gives a changed value as a change over time (moved, now, switched, increased, replaced), so the EXISTING one is no longer true ("deadline is 15 Mar" then "deadline moved to 1 Apr"; "database is budget.db" then "database is now my_budget_tracker.db").
- update, also: the NEW item settles what an EXISTING item called pending, undecided, open or awaiting a decision, so the EXISTING one is no longer true (EXISTING "Whether the CHF code fix goes into 4.2.0 is pending a user decision." NEW "The user deferred the CHF code fix to 4.2.1 on 22 Oct." -> update).
- contradiction: incompatible claims about the same thing with nothing indicating a change over time, so both cannot be true and neither replaces the other ("I have never used Flask-Login" and "Flask-Login v0.6.2 handles our sessions"; "I have an API key" and "I never got an API key"). Both will be kept and shown side by side.
- extends: the NEW item adds detail to the EXISTING one (a reason, a condition, a consequence, an exception or a specific) without changing or replacing its value, so both stay true and the NEW one is about the EXISTING one ("Retries cap at 3" then "The retry cap exists because the broker rate-limits"; "Refunds over $500 need a human approver" then "The approver for large refunds is the finance on-call"). A separate fact that only shares a subject is different, not extends.
- different: anything else. Two facts about the same subject are different when both can be true at once ("the deadline is 15 Mar" and "Craig asked for a schedule to meet it"; a plan and a later question about it). Measurements or settings of different things are different even when their values look alike: another component, endpoint, environment, tool or scope ("autocomplete API response time is 280ms" and "weather app API response time is 220ms after caching"; "fetch latency 250ms" and "autocomplete latency 280ms"); update only when the NEW item measures or sets the very same thing. When unsure between update and different, answer different: wrongly retiring a true fact costs more than keeping two."""



SESSION_STATE_SYSTEM = """You keep the working state of one live agent session current, so the agent can carry on after its context is compacted or reset.

Return ONLY a JSON object:
{
  "goal": string,             // what the session is trying to accomplish, one sentence
  "decisions": [string],      // decisions taken so far, each one sentence, with values and names
  "open_questions": [string], // unresolved questions or blockers
  "in_play": [string],        // files, systems, people or artifacts currently being worked on
  "recent_facts": [string],   // concrete facts learned in the latest turns worth keeping (dates, numbers, names)
  "progress_claims": [string] // things the assistant says it finished, kept separate: unverified
}

Rules: update the previous state with the new turns; keep what is still true, drop what was resolved or replaced. Be concrete. At most 8 entries per list. No markdown."""


def session_state_user(previous_state: str, turns: str) -> str:
    return f"Previous state:\n{previous_state}\n\nNew turns:\n{turns}"


def reconcile_user(new_statement: str, new_date: str, existing: list[tuple[str, str]]) -> str:
    """existing: (date, statement) pairs, numbered from 1 in the prompt."""
    lines = [f"EXISTING {i} ({date}): {statement}" for i, (date, statement) in enumerate(existing, 1)]
    return "\n".join(lines) + f"\nNEW ({new_date}): {new_statement}"


# ------------------------------------------------------------------ threads and knowledge
# Real sessions taught us that most "facts" pulled from an excerpt are steps of the work and dated
# status snapshots. So the work itself goes on one living page per thread (a release, task, PR,
# ticket or topic), edited in place, and only what a teammate needs a week later is kept as knowledge.

THREADS_SYSTEM = """You maintain the living pages of a team's pieces of work (threads): one page per release, task, pull request, ticket or topic, edited in place as conversations about it happen. You get one new conversation excerpt, the current pages of the threads it may concern, and candidate keys found in the text.

Return ONLY a JSON object: {"threads": [<page>, ...]} with one full page for every thread this excerpt changes or starts. Leave out threads it does not change. {"threads": []} is a valid answer: small talk, tooling chatter and one-off questions start no thread.

A thread is a piece of WORK: something being done across steps or sessions (a release, a task, a pull request, a ticket, a branch, a multi-step effort such as a migration or an investigation). A "topic:" page needs work in progress too: start one only when the excerpt shows something being done about it (a goal pursued, steps taken or planned, a question being worked on). Never start a page for a single statement of a fact, rule, decision or preference ("the production database is on port 5544", "the frontend uses SvelteKit", "always deploy with make deploy"): those are knowledge, kept elsewhere, and an excerpt that only states them starts no thread. Such a statement goes on an existing page only when it changes that work.

<page>:
{
  "key": string,        // an existing page's key, or a new one: "branch:<name>", "pr:<number>", "ticket:<ID>", "release:<version>", or "topic:<short-slug>" when nothing better exists. Prefer a candidate key.
  "kind": "release" | "task" | "pr" | "ticket" | "topic",  // chosen only for topic keys; other keys fix it (release:, pr:, ticket:, branch: = task)
  "title": string,      // short and specific: "Release 4.2.0 (billing)", "Retry budget for the export worker"
  "retitle": boolean,   // optional, default false: an existing page keeps its title. true only when the work itself changed (re-scoped, renamed), never because this excerpt is about one part of it
  "status": "open" | "blocked" | "done" | "abandoned",
  "summary": string,    // 2-3 sentences about this work only, current state first, then what matters most; never the project's description or stack, never what kind of note this is ("This is a configuration fact recorded for ...")
  "sections": [{"label": string, "items": [{"text": string, "date": "YYYY-MM-DD" | null, "evidence": [{"episode_id": string | null, "quote": string}]}]}],
  "aliases": [string],  // other keys naming the same work (its branch, PR, ticket)
  "related": [string]   // keys of other threads this one depends on or contains
}

Section labels, only these, only when non-empty, in this order: Goal, State, Contains, Done, Decisions, Left out, Deferred, Open questions, Next steps, Outcome.

Rules:
- Return the whole updated page, not a diff. Keep what is still true from the current page, change what the excerpt changes, newest facts win. At most 8 items per section (State 3, Done 4): merge or drop the least useful.
- One page per piece of work. A PR or ticket that delivers work already tracked by a page (the release PR of a release, the task's own PR, the ticket the task implements) goes on that page: put its key in that page's aliases, record it in State or Done ("Release PR #454 opened to master"), and give it no page of its own. A separate page only for a PR or ticket that is its own piece of work: a teammate's feature PR pulled into a release is listed under the release's Contains, and gets its own page only if the conversation works on it as a task of its own.
- One item per point. When the excerpt restates an item already on the page, edit that item; never add a reworded copy ("The user merges #454 into master." beside "The user merges release PR #454 into master").
- State is 1-3 short lines on where things stand now ("QA passed; waiting for the merge of #454"). No commit SHAs, no test counts, no command output.
- Done is at most 4 outcomes a teammate cares about ("Sam's review comments fixed and re-review requested", "Release PR #454 opened to master"), not a log of steps: no commit SHAs, no test, component or coverage counts, no command output.
- Contains says what is in, by PR or ticket and one short phrase ("#448 QA fixes for the export screen"), no SHAs.
- An open question the excerpt answers, or a next step it completes, leaves Open questions / Next steps: record the result under Done, Decisions or Outcome, or drop it.
- Messages of role "tool" ("[tools] ...") summarize tool runs: use them to tell what really happened (a test that failed or passed), never copy them onto the page.
- Progress is at most one line in State or Done ("cherry-picks for 4.2.0 are in; QA passed"), never a list of steps. Commit SHAs, test counts and command output stay out unless they are the outcome.
- Every item needs evidence. A new or changed item quotes the excerpt: "episode_id": null and a verbatim span of 5-200 characters copied character for character. An item kept from the current page keeps its evidence exactly as given. Items without valid evidence are discarded.
- Status: done when the work shipped or merged, abandoned when dropped, blocked when waiting on something outside the team's control.
- Use absolute dates. Only what the excerpt and the pages say; nothing from outside knowledge."""


def threads_user(label: str, occurred_at: str, content: str, project: str, keys: list[str], pages: list[dict]) -> str:
    head = f"Project: {project}\nEpisode: {label}\nEpisode date: {occurred_at}\n"
    head += f"Candidate keys: {', '.join(keys) if keys else '(none)'}\n\n"
    head += "<pages>\n" + (json.dumps(pages, ensure_ascii=False, indent=1) if pages else "(no existing page matches)") + "\n</pages>\n\n"
    return f"{head}<excerpt>\n{content}\n</excerpt>"


KNOWLEDGE_SYSTEM = """You decide what, if anything, from one conversation excerpt goes into a team's long-term memory. Memory is read by every future agent session on this project, on every task, so each item costs attention forever. Too much memory is worse than too little: an agent that reads ten irrelevant items misses the one that matters. The work itself (what was done, what is in progress, what was found while doing it) is tracked elsewhere on the page for that piece of work; it is never memory.

The normal answer is [] or one item. Most excerpts, especially coding sessions, contain no long-term knowledge at all. Return more than three items only if the excerpt really settles that many separate team-wide decisions or rules.

An item is kept only if it passes all three tests:
1. Another task. An agent working on a different task in this project next month would act differently because of it. If it only matters to the task, bug, ticket, PR or release being worked on here, it is not memory.
2. Not in the code. Reading the code, config, README, docs, ticket or git history for a minute would not tell them. Never describe what the code does, where something is implemented, file and line numbers, call chains, or how a feature works: the code says that and stays current, memory goes stale. The one exception is a trap: code, a name or a setting that misleads a careful reader into the wrong conclusion (a gotcha).
3. Still true later. It stays true after this task is done: not a state of the moment, a result of one run, or something about to change.

What passes:
- decision: a team decision about how things are done from now on, with its reason ("We map invoice field aliases with the model, not a lookup class: the class kept drifting from the prompt"). Not a decision about one task's scope or one release's contents.
- rule: a standing always/never for this project ("Never deploy to the shared demo environment with --skip-migrations").
- gotcha: a trap in the environment, tools, process or code that will catch the next person too, with the way around it ("The test harness's saved `qa` connection actually points at the staging database").
- preference: how a person on the team likes things done.
- fact: only about the world outside the code that the code cannot show: shared resources and who else uses them, which environment or account is which, ownership, external deadlines and vendor changes that affect the project ("The payment provider turns on a 30-day idle refresh-token limit in production from 4 Nov 2026").

Never memory, however true or interesting:
- the work and its findings: root-cause analyses and code traces of a bug, what a fix changes, what a PR contains, test or QA results, how many runs passed, what was reproduced, a known bug (it belongs in its ticket), a release's scope, merge order, who is doing what, workers or agents started, plans, proposals, options, open questions, next steps.
- states of the moment: "as of <date> ...", what is deployed where right now, which version is active, what is merged or unmerged, what is broken today, what something is waiting on.
- descriptions of the code, of a tool the team built, or of how to run it (commands, flags, file layout, which model an agent uses): those are in the repo.
- general knowledge of a platform or vendor (where a setting is in a vendor's UI, how a protocol works) unless it is a trap specific to this project's setup.
- the assistant's own tooling, shell and editor behaviour, scripts and how it parsed output.
- run-specific trivia: temp paths, IDs of sessions, jobs, runs or records, one-off values, timings and counts.
- status and completion claims, and anything an assistant only claims to have done.
- the same lesson twice, or one that <context> shows memory already holds.

Coding sessions. A message of role "tool" is the client's one-line summary of the tool work between two replies ("[tools] Read ok: src/x.py; Bash `pytest -q` failed (exit 1): E connection refused ..."). Use it to see what happened, never as an item. A failure followed by a fix is memory only when the trap will catch the next person on a different task (an environment or tooling trap such as "the tests need Postgres on 5433; 5432 is another app's"), not when it was a bug in the code being changed.

Writing an item: one self-contained statement that makes sense with no other context. Name the system or person; absolute dates. Put the reason in "why". A statement never carries undecided status ("pending a decision", "not yet decided", "awaiting"); if the question is not decided, there is nothing to keep.

Return ONLY a JSON array (possibly empty). Each element:
{
  "statement": string,
  "category": "decision" | "rule" | "gotcha" | "fact" | "preference",
  "why": string,           // one clause: the reason behind it or why it matters
  "thread_key": string | null, // the key of the listed thread it came up in, if any
  "scope": "project" | "thread", // "project" for nearly everything kept; "thread" only for a rule that applies inside that piece of work alone
  "subjects": [string],    // 1-3 short lowercase names of what it is about
  "assertion": "stated" | "observed" | "claimed" | "inferred",
  "confidence": "high" | "medium" | "low",
  "valid_from": string | null,
  "valid_until": string | null, // ISO date the item itself says it stops being true ("the scratch org expires on 28 Oct 2026", "until the freeze ends on 3 Nov 2026"), else null; a date something starts ("from 4 Nov") is valid_from
  "standing": boolean,     // true for a rule the assistant must follow from now on ("always ...", "never ...")
  "quote": string          // a verbatim span of the excerpt, 5-200 characters, copied character for character
}

Judge relevance against the project profile. Use <context> only to understand the excerpt, never as a source."""


def knowledge_user(label: str, occurred_at: str, content: str, project: str, threads: list[tuple[str, str]],
                   context: str = "") -> str:
    head = f"Project: {project}\nEpisode: {label}\nEpisode date: {occurred_at}\n"
    if threads:
        head += "Threads this excerpt touched:\n" + "\n".join(f"- {key}: {title}" for key, title in threads) + "\n"
    head += "\n"
    if context:
        head += f"<context>\n{context}\n</context>\n\n"
    return f"{head}<excerpt>\n{content}\n</excerpt>"
