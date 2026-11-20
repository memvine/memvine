# What Memvine can and cannot do

As of 14 November 2026. Each claim here is either checked by something runnable in this repository
or listed under "cannot".

## In one paragraph

Memvine is memory for AI agents. Agents and people talk; Memvine keeps the few things that stay
true, ties each one to the words it came from, and hands the next task only what it needs. It is
strongest as shared memory for a team's coding agents. It also stores long conversations between a
person and an assistant.

## What it can do

### Take in what was said

| Capability | How |
| --- | --- |
| Capture coding sessions as they happen | Claude Code plugin and OpenAI Codex CLI plugin |
| Bring in earlier sessions | `/memvine:backfill` in the Claude Code plugin |
| Take conversation from your own agent | TypeScript and Python client libraries, or the HTTP API |
| Work with any MCP client | `/mcp`: recall, remember, session brief, forget |
| Add something by hand | The console's Add tab |
| Keep secrets out | Keys, tokens and private keys are redacted from every text before it is stored |

### Decide what is worth remembering (coding mode, the default)

- A memory is kept only if an agent on a different task would act differently because of it, the
  code and documents could not have told it, and it stays true after the task. Most sessions leave
  nothing or one item.
- Five kinds of knowledge: decision, rule, gotcha, fact, preference.
- Work in progress (a release, a ticket, a pull request) gets one living page, not a pile of facts.

### Keep each memory accountable

- **Evidence.** Every memory carries the exact quote it came from, checked against the source, and
  who said it: a person, a tool run, the assistant unverified, or inferred.
- **Change over time.** A newer value replaces an older one and the older is kept as history. Two
  statements that conflict, with neither replacing the other, are both kept and shown together.
- **Tied to the code.** A memory about a file records the version it depended on. When the file
  changes, the memory is marked possibly outdated until someone restates or confirms it.
- **People stay in charge.** Keep, Wrong or Forget on any memory. A forgotten memory is never
  learned again. A background tidy-up merges duplicates and flags doubtful items.

### Give memory back

| Capability | What happens |
| --- | --- |
| Recall for an agent | A short briefing for the task in hand, within a token budget. A fast model picks what the task needs. |
| Say nothing | An unrelated question gets no briefing instead of loosely related noise. |
| Standing rules | "Always do X" rules come with every answer, in their own section. |
| Ask, for a person | The console's Ask tab and `POST /v1/ask`: the model answers from memory only, shows what it relied on, and says so when memory does not hold the answer. |
| Look back | Recall as of a past moment, or with history included. |
| Start a session informed | A brief of who the person is and what the project is; a fresh brief after the context is compacted. |

### Hold a long conversation (conversation mode, chosen per project)

- Stores the whole conversation and searches the original words by keyword and by meaning, one
  passage per turn.
- Keeps a small profile of the person beside it: who they are, the people in their life, goals,
  limits, preferences and decisions, each the value of a named slot. Only what the person said
  counts, a changed value replaces the old one and keeps it as history, and two statements that
  cannot both be true are kept together. A new item is written only for what it adds.
- Tidies that profile in the background: lines about the same thing become one line, the members of
  a set the person builds up become a list with its count, a plan whose day has passed stops being
  current, and a line no briefing serves for a long while ranks lower. Any merge can be undone.
- Applies the person's standing instructions ("always name the narrator") to every answer.
- Answers "summarize how this went" from digests of the conversation, in order.

### Work for a team and be run by one

- Organizations, people with roles, invite links, and audiences that decide who may read which memory.
- A person's sessions can stay their own: a personal project only they open, and a team space that
  refuses sessions and holds only the memories people chose to share, each with who said it. Memvine
  suggests what to share after a session; a team can also have the suggested ones shared by themselves.
- Each project is isolated from every other, enforced in the database, and every administrative
  change is in an audit log.
- A console to read memories, sources, activity, requests and usage.
- Usage counted per project with an optional monthly cap; old source text can be dropped after a
  set number of days while the memories stay.
- Hosted at memvine.dev for invited teams, or self-hosted with Docker Compose. Nightly backups that
  are restore-tested, and a smoke test to run after each deploy.

## What has been measured

| Check | Result |
| --- | --- |
| Memory check for coding mode: what is kept, dropped, updated and recalled, with a real model | 28 of 28 |
| Agent test: Claude Code answering questions about a project | 9 of 9 right with Memvine, 0 of 9 without |
| Smoke test on the hosted service, including Ask and conversation mode | 18 of 18 |

## What it cannot do

### Limits of the memory itself

- **It costs model calls to take a conversation in.** Plain search needs only embeddings.
- **The profile in conversation mode is new and barely measured.** A profile holds 45 to 80 items
  for a long conversation, and some still overlap.
- **A changed value is tracked only when the model sees the change.**
- **It can serve a wrong memory.** Unreviewed memories are served; review is feedback, not a gate.
- **Quality depends on the model.** Memvine is built and measured on Anthropic models (an API key,
  or a Claude subscription on a local machine). Any server that speaks the OpenAI API can be used
  in their place, but nothing here has been measured on another model.
- **Keyword search assumes English.**

### Limits of the evidence

- The coding-mode checks are built by the author. Nobody outside has yet used Memvine on real
  work for long enough to show it helps a team.
- Sharing from a personal project to a team space was tried once end to end with a real model, on
  one invented session. Whether the suggestions are the right ones, and whether people act on
  them, has not been measured.

### Not built

- It does not read a repository, documents, PDFs, tickets or web pages by itself. Memory comes from
  conversation and from what someone adds.
- No Cursor plugin, and no sign-in flow for using it from Claude.ai or ChatGPT.
- A memory changed in a person's own project reaches the team space only when the new one is
  shared (it then replaces the team's copy); a team that wants that without a step sets sharing to
  automatic. Forgetting your own copy of a shared memory does not take it back from the team.
- The client libraries are in the repository but not published to npm or PyPI.
- Ask is in the console and the HTTP API only: not yet in the client libraries, the MCP tools or
  the plugins.
- No password reset by email, no email verification, no single sign-on. The hosted service is
  invite-only.
- No billing and no paid plans: Memvine is an open-source project.
- A graph of how memories, people and code connect is designed and on hold.
- The terms and privacy pages are drafts a lawyer has not reviewed, and there is no security
  certification.

## Who it is for today

- **Teams whose coding agents keep re-learning the same things:** conventions, traps and decisions
  that are not in the repository. This is the part that is measured and works.
- **People building assistants that talk to the same person over time,** who want a transcript they
  can search, standing instructions that are always applied, and answers that show their source.
  The person-centred profile meant to make Memvine better than search here is built and still being
  measured ([conversation-memory-plan.md](plans/conversation-memory-plan.md)).
