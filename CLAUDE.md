# Memvine: notes for agents working on this repo

Memvine is a hosted team memory service for coding agents. Client plugins (Claude Code, Codex) send
session conversation to a server, which turns it into a small set of durable memories plus pages
for work in progress, and serves recall back into each session.

## Layout

- `server/`: Python 3.12, FastAPI, Postgres + pgvector. Ingest → synthesis (`synthesis.py`:
  thread pages via `threads.py`, knowledge via `extraction/`) → reconcile (`reconcile.py`) →
  recall (`recall.py`). Migrations in `server/memvine_server/migrations/` (numbered, applied on
  start; never edit an applied one, add a new file).
- `console/`: Next.js 15 customer console (`lib/api-live.ts` talks to the server; `lib/api-mock.ts`
  when `MEMVINE_API_URL` is unset).
- `plugins/claude-code`, `plugins/codex`: the agent plugins (hooks send turns, recall each prompt).
- `sdk/typescript`, `sdk/python`; `benchmark/`: the memory check and evaluation harnesses.
- `site/`: the memvine.dev homepage (static HTML, CSS and a canvas vine; nginx Dockerfile).
- `legacy/cli/`: the earlier git-native CLI (npm `memvine`), a separate tool with its own package,
  tests and benchmark. Not part of the service; leave it alone unless asked.
- `deploy/`: self-hosting with Docker (`deploy/README.md`); `deploy/laptop/` (a team server on one
  Mac). The hosted service's own setup is in the private repository `memvine/memvine-cloud`.
- `docs/how-memory-works.md`; `docs/plans/` holds parked plans (graph work: on hold, below).

## Setup and tests

```bash
scripts/dev-setup.sh                       # Postgres+pgvector on :5433, server venv, console packages
cd server && .venv/bin/python -m pytest -q  # tests use a fake model and fake embedder
.venv/bin/ruff check memvine_server tests ../benchmark
cd ../console && npx tsc --noEmit && npm run build
node --test plugins/codex/bin/ plugins/claude-code/bin/
```
Give parallel test runs their own database: `MEMVINE_TEST_DATABASE_URL=postgresql://memvine:memvine@localhost:5433/memvine_test_<name>`.

GitHub Actions are manual-only (the account's quota ran out): never rely on CI; run checks locally.

## The memory check: the gate for extraction and recall changes

```bash
cd server && .venv/bin/python ../benchmark/memory/check.py            # ~5 min, real model
cd server && .venv/bin/python ../benchmark/memory/check.py --agent-e2e  # + real Claude Code with/without memory (~30 min)
```
Real model calls: `MEMVINE_LLM_BACKEND=claude-cli` (a Claude subscription, local machine) or
`anthropic` with `ANTHROPIC_API_KEY`. (`openai` is any OpenAI-compatible server, for people who
self-host; nothing is measured on it, and quoted results are Claude's.) Last results: 28/28 (16 Nov); agent test 9/9 right with memory,
0/9 without. Don't ship a change to extraction, reconciliation or recall without running it.

## Decisions to keep

- **Two modes, chosen per project** (`projects.memory_mode`). `coding` (the default) keeps little, as
  the next decisions say. `conversation` is for one person's long conversation with an assistant: the
  conversation is stored whole and searched word for word, and memory holds only a small profile of
  the person, each item the value of a named slot (`CONVERSATION_SYSTEM`, `extract_profile`,
  `slots.py`, the `detail` path in `recall.py`), tidied in the background into merged lines and
  lists with counts (`profile_consolidation.py`; coding-mode consolidation does not run there). It has no thread pages, recalls the few profile
  lines a question needs and then source excerpts, and answers a summary question from digests of
  the conversation (`digests.py`). Its first version kept every detail and scored below plain search
  (`docs/plans/conversation-memory-plan.md`). BEAM measures
  conversation mode; the memory check measures coding mode. A change must not move one mode's
  behaviour into the other.
- **In coding mode, too much memory is worse than too little.** A memory is kept only if it passes three tests:
  an agent on a *different* task would act differently because of it; the code, config, README,
  ticket or git history can't tell them (except traps where the code misleads); it is still true
  after the task. Work in flight, bug traces, test results, PR contents, release scope, "as of"
  states and descriptions of code are never memory. Prefer missing a marginal item over storing
  noise. (`KNOWLEDGE_SYSTEM` in `extraction/prompts.py`; cases in `benchmark/knowledge/cases.py`.)
- **One memory model.** Thread pages are for *work* (release, task, PR, ticket, multi-step effort),
  never for a single fact; durable knowledge (decision, rule, gotcha, fact, preference) is memory.
  There is no "v1/v2" anywhere.
- **In coding mode, recall carries what the question needs and nothing twice**, abstains on unrelated questions,
  and keeps standing rules in their own always-on section.
- **A person's sessions are theirs.** On a team, sessions go to a personal project only its owner
  opens (`projects.owner_user_id`; `auth.find_project` is the one gate), and the team's project is
  a team space that refuses conversation and holds what people shared (`projects.team_space`,
  `refuse_conversation`). The plugins send sessions to the person's own project only, read both on
  recall, and share one memory at a time with its author and quoted line (`share` in
  `plugins/claude-code/bin/memvine-hook.mjs`). Memvine suggests what to share; the agent never shares
  on its own. A team may set `share_policy: auto`. A new route that reads a project must stay behind
  `require_project` or `find_project`: `test_personal_projects.py` aims every project route at
  someone else's personal project.
- **Unreviewed memories are served.** Review (Keep / Wrong / Forget) is feedback, not a gate;
  forgotten memories become private drop cases and are never re-learned.
- **Graph work is on hold** (`docs/plans/`): plain-text memory must work for agents first.
- Examples in code, tests and docs use the invented "Northwind" billing world. Never put real
  company, customer or employer details in the repo.

## Status (16 Nov 2026)

Priorities and what's next: `ROADMAP.md` and the GitHub issues labelled now/next/later.

- A hosted instance runs at `app.memvine.dev` (API `api.memvine.dev`, site `www.memvine.dev`),
  open to invited teams only (`MEMVINE_SIGNUP=invite`). How it is deployed, backed up and run lives
  in the private repository `memvine/memvine-cloud` (`HOSTING.md`, runbook): never put hosting
  details (providers' service names, buckets, DNS, runbooks) back in this repository.
  After each deploy: `MEMVINE_SMOKE_KEY=mv_mk_... python3 benchmark/smoke/hosted.py --api <url> --console <url>`
  (throwaway project, deleted at the end; `--no-synthesis` skips the real model; `--conversation`
  adds conversation mode). Last run 16 Nov, with `--conversation`: 23/23, the team-space steps among
  them. Sharing from the console was checked by hand (share, refuse a second share, a changed
  memory replacing the team's copy, take back); the smoke test does not cover that.
- The server image installs `server/requirements.lock`; rebuild it after changing `pyproject.toml`
  (`server/README.md`, Docker).
- `site/terms.html` and `site/privacy.html` are drafts a lawyer has not reviewed.
- Pending, the operator's: a Slack webhook for alerts, an outside uptime check, a monthly limit on
  the Anthropic key, re-pointing each laptop's plugin (#2).
- The repository is private. Making it public and publishing packages happen only on the owner's
  explicit go, after their own end-to-end test; an agent never does either on its own. What is ready
  for that day: a secrets scan of the whole history and a read of every issue found nothing that
  must not be public (15 Nov); `@memvine/sdk` (npm) and `memvine-sdk` (PyPI) build and pass their
  checks and neither name is taken (#11). After it is public: switch on private vulnerability
  reporting (`SECURITY.md` points to it; GitHub offers it on public repositories only).
- Later: password reset by email, SSO. The core stays open source (MIT) and complete for
  self-hosting; a managed service may be built on it later, with any code only it needs kept in a
  separate private repository (decided 18 Nov).
